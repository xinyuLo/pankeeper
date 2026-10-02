"""115 网盘客户端（原创实现，webapi 路线，不引 p115client 重依赖）。

端点均为 115 网页/App 公开接口（接口事实，调研见 docs/research/115-tgtodrive-cloudsaver.md）：
- GET  https://my.115.com/?ct=ajax&ac=get_user_aq     取 uid（兼做 Cookie 探活）
- GET  https://webapi.115.com/share/snap              分享内容清单（offset 翻页到 count）
- POST https://webapi.115.com/share/receive           整组接收（file_id 逗号拼接）
- GET  https://webapi.115.com/files                   自己网盘目录浏览（路径→cid）
- POST https://webapi.115.com/files/add               建目录

关键语义（社区实测）：
- 接收码：链接形如 115.com/s/<code>?password=<rc>，receive_code 即 password；缺省传空。
- file_id 取法：**文件夹优先取 fid**（分享侧目录 id，可整目录接收），文件取 cid。
- 幂等：返回 error 含「文件已接收，无需重复接收」按成功（计 skip）。
- 风控：**遇验证码 = 已被风控，停下报警不硬闯**；每请求 ≥0.8s 串行门。
- UA：微信小程序 UA（webapi 伪装口径）。
"""
from __future__ import annotations

import hashlib
import re
import threading

import httpx

from ..security import decrypt_credential
from ..services import reqstat
from .base import AdapterError, CloudAdapter, CredentialExpired, ShareBanned, ShareFile, TaskSpec, TransferResult
from .rate_gate import RateGate

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "MicroMessenger/6.8.0(0x16080000) MiniProgramEnv/Mac MacWechat/WMPF"
)
REFERER = "https://servicewechat.com/wx2c744c010a61b0fa/94/page-frame.html"

SHARE_RE = re.compile(r"(?:115|115cdn|anxia)\.com/s/(\w+)(?:\?password=(\w+))?")
ALREADY = "文件已接收，无需重复接收"
PAGE = 1000

_CLIENTS: dict[str, httpx.Client] = {}
_CLIENT_LOCK = threading.Lock()


def _shared_client(cookies: str) -> httpx.Client:
    key = hashlib.sha1(cookies.encode()).hexdigest()
    with _CLIENT_LOCK:
        cli = _CLIENTS.get(key)
        if cli is None:
            cli = httpx.Client(
                headers={"cookie": cookies, "user-agent": UA, "referer": REFERER, "xweb_xhr": "1"},
                timeout=20.0,
                event_hooks={"request": [reqstat.hook("115")]},
            )
            _CLIENTS[key] = cli
        return cli


def parse_share_url(url: str) -> tuple[str, str]:
    """分享链接 → (share_code, receive_code)。无法解析抛 AdapterError。"""
    m = SHARE_RE.search(url or "")
    if m is None:
        raise AdapterError(f"无法识别的 115 分享链接（需形如 115.com/s/xxx?password=yyy）：{(url or '')[:80]}")
    return m.group(1), m.group(2) or ""


class Pan115Adapter(CloudAdapter):
    type = "115"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)
        self.gate = gate or RateGate("115", min_interval=1.0, cooldown=30.0)
        self._http = _shared_client(self.cookies)
        self._uid: str | None = None

    # ---------- 基础请求 ----------

    def _get(self, url: str, params: dict | None = None) -> dict:
        self.gate.wait()
        try:
            resp = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code == 406:
            # 115 限频信号：让 RateGate 进退避，调用方按普通失败处理
            self.gate.on_failure()
            raise AdapterError("115 限频（HTTP 406），请稍后再试")
        try:
            data = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        self.gate.on_success()
        return data

    def _post(self, url: str, data: dict) -> dict:
        self.gate.wait()
        try:
            resp = self._http.post(url, data=data)
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        try:
            body = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        self.gate.on_success()
        return body

    @staticmethod
    def _check_state(data: dict, action: str, *, already_ok: bool = False) -> bool:
        """webapi state=false 分流。返回 True 表示「已接收」类幂等成功。"""
        if data.get("state", True):
            return False
        err = str(data.get("error") or data.get("error_msg") or "")
        if already_ok and ALREADY in err:
            return True
        if "验证码" in err:
            raise AdapterError(f"115 已触发验证码（疑似风控），停止操作并告警：{err}")
        if "登录" in err or "身份" in err or "cookie" in err.lower():
            raise CredentialExpired(f"115 Cookie 已失效：{err}")
        raise AdapterError(f"{action}失败：{err or data}")

    # ---------- 接口实现 ----------

    def verify(self) -> str:
        data = self._get("https://my.115.com/", {"ct": "ajax", "ac": "get_user_aq"})
        self._check_state(data, "探活")
        uid = str((data.get("data") or {}).get("uid") or "")
        if not uid:
            raise CredentialExpired("115 Cookie 已失效（拿不到 uid）")
        self._uid = uid
        return (data.get("data") or {}).get("u_name") or f"115账号{uid}"

    def uid(self) -> str:
        if self._uid is None:
            self.verify()
        return self._uid or ""

    def list_share(self, spec: TaskSpec) -> list[ShareFile]:
        share_code, receive_code = parse_share_url(spec.share_url)
        files = self._snap_page(share_code, receive_code, cid="")
        roots = [self._row_to_file(r, "") for r in files]
        out = list(roots)
        if spec.include_subdirs:
            for node in [f for f in roots if f.is_dir]:
                out.extend(self._walk_share_dir(share_code, receive_code, node.fid, node.name))
        if spec.exclude_names:
            out = [f for f in out if f.name not in spec.exclude_names]
        # 接收上下文存给 save_files
        self._share_ctx = {"share_code": share_code, "receive_code": receive_code}
        return out

    def _snap_page(self, share_code: str, receive_code: str, cid: str) -> list[dict]:
        """share/snap 翻页到 count（别学 limit=20 不翻页的反面教材）。"""
        rows: list[dict] = []
        offset = 0
        for _ in range(100):  # 10 万项封顶
            data = self._get(
                "https://webapi.115.com/share/snap",
                {"share_code": share_code, "receive_code": receive_code, "cid": cid, "offset": offset, "limit": PAGE},
            )
            self._check_state(data, "分享清单")
            page = data.get("list") or []
            rows.extend(page)
            count = int(data.get("count") or 0)
            offset += len(page)
            if not page or offset >= count:
                return rows
        return rows

    def _walk_share_dir(self, share_code: str, receive_code: str, folder_fid: str, base: str) -> list[ShareFile]:
        out = [self._row_to_file(r, base) for r in self._snap_page(share_code, receive_code, folder_fid)]
        full: list[ShareFile] = []
        for f in out:
            full.append(f)
            if f.is_dir:
                full.extend(self._walk_share_dir(share_code, receive_code, f.fid, f"{base}/{f.name}" if base else f.name))
        return full

    @staticmethod
    def _row_name(row: dict) -> str:
        return row.get("n") or row.get("fn") or ""

    @staticmethod
    def _is_file_row(row: dict) -> bool:
        """文件条目带哈希字段，目录没有。"""
        return bool(row.get("sha") or row.get("sha1"))

    @staticmethod
    def _row_to_file(row: dict, base: str) -> ShareFile:
        """snap 行 → ShareFile。file_id 取法：文件夹优先 fid，文件取 cid（TgtoDrive 实测语义）。"""
        name = Pan115Adapter._row_name(row)
        is_dir = bool(row.get("fid")) and not Pan115Adapter._is_file_row(row)
        fid = str(row.get("fid") or row.get("cid") or "")
        return ShareFile(fid=fid, name=name, is_dir=is_dir, size=int(row.get("s") or 0),
                         path=(base + "/" + name).lstrip("/") if name else base, md5="")

    def list_dir_names(self, dir_path: str) -> set[str]:
        """目标目录现有文件名集合（目录不存在/为空返回空集）。"""
        try:
            cid = self.path_to_cid(dir_path)
        except AdapterError:
            return set()
        return {self._row_name(r) for r in self._list_own_dir(cid) if self._is_file_row(r)}

    def _list_own_dir(self, cid: str) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        for _ in range(50):
            data = self._get(
                "https://webapi.115.com/files",
                {"aid": 1, "cid": cid, "o": "user_ptime", "asc": 1, "offset": offset, "show_dir": 1, "limit": PAGE, "format": "json"},
            )
            self._check_state(data, "列目录")
            page = data.get("data", {}).get("list") or []
            rows.extend(page)
            count = int(data.get("data", {}).get("count") or 0)
            offset += len(page)
            if not page or offset >= count:
                return rows
        return rows

    def path_to_cid(self, dir_path: str) -> str:
        """网盘路径 → cid（逐层下钻；根=0）。不存在抛 AdapterError。"""
        cid = "0"
        for seg in [p for p in (dir_path or "").strip("/").split("/") if p]:
            rows = self._list_own_dir(cid)
            match = next(
                (r for r in rows if self._row_name(r) == seg and r.get("fid") and not self._is_file_row(r)),
                None,
            )
            if match is None:
                raise AdapterError(f"115 目录不存在：{dir_path}（缺 {seg}）")
            cid = str(match.get("fid"))
        return cid

    def _mkdir(self, parent_cid: str, name: str) -> str:
        """webapi 建目录，返回新目录 cid；返回体缺 cid 时回落父目录查找（已存在场景）。"""
        body = self._post("https://webapi.115.com/files/add", {"pid": parent_cid, "dirname": name})
        self._check_state(body, "建目录")
        new_cid = str((body.get("data") or {}).get("file_id") or (body.get("data") or {}).get("cid") or "")
        if new_cid:
            return new_cid
        for r in self._list_own_dir(parent_cid):
            if self._row_name(r) == name and r.get("fid") and not self._is_file_row(r):
                return str(r.get("fid"))
        raise AdapterError(f"建目录后找不到 cid：{name}")

    def ensure_dir(self, dir_path: str) -> str:
        """逐级建目录，返回末级 cid。"""
        cid = "0"
        for seg in [p for p in (dir_path or "").strip("/").split("/") if p]:
            rows = self._list_own_dir(cid)
            match = next(
                (r for r in rows if self._row_name(r) == seg and r.get("fid") and not self._is_file_row(r)),
                None,
            )
            if match:
                cid = str(match.get("fid"))
            else:
                cid = self._mkdir(cid, seg)
        return cid

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        ctx = getattr(self, "_share_ctx", None)
        if not ctx:
            raise AdapterError("分享上下文缺失（save_files 必须跟在 list_share 之后）")
        result = TransferResult()
        save_list = [f for f in files if not f.is_dir and f.fid]

        # 去重：目标目录（或 compare_path）现有文件名
        base = spec.compare_path or spec.save_dir
        existing: set[str] = set()
        if base:
            try:
                existing = self.list_dir_names(base)
            except (AdapterError, CredentialExpired) as e:
                on_log(f"对比目录 {base} 读取失败（{e}），本次不做去重基线比对")
        need = [f for f in save_list if f.name not in existing and (f.target_name or f.name) not in existing]
        result.skip = len(save_list) - len(need)
        on_log(f"清单 {len(save_list)} 项：去重跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

        # 建目录：按相对目录分组，逐组整组接收（115 一次可整目录/多文件提交）
        by_dir: dict[str, list[ShareFile]] = {}
        for f in need:
            rel = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            by_dir.setdefault(rel, []).append(f)
        target_cid = self.ensure_dir(spec.save_dir)
        done, total = 0, len(need)
        for rel, group in by_dir.items():
            cid = target_cid
            if rel:
                cid = self.ensure_dir(spec.save_dir.rstrip("/") + "/" + rel)
            self._receive_group(ctx, group, cid, result, on_log)
            done += len(group)
            on_progress(min(99, int(done / total * 100)))
        on_progress(100)
        return result

    def _receive_group(self, ctx: dict, files: list[ShareFile], cid: str, result: TransferResult, on_log) -> None:
        if not files:
            return
        body = self._post(
            "https://webapi.115.com/share/receive",
            {
                "user_id": self.uid(),
                "share_code": ctx["share_code"],
                "receive_code": ctx["receive_code"],
                "file_id": ",".join(f.fid for f in files),
                "cid": cid,
            },
        )
        if self._check_state(body, "转存", already_ok=True):
            on_log("全部文件此前已接收过，无需重复接收（计为跳过）")
            result.skip += len(files)
            return
        result.add += len(files)
        for f in files:
            result.transferred.append({"name": f.target_name or f.name, "fid": f.fid})

    def summary(self) -> dict:
        return {}
