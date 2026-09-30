"""百度网盘客户端（原创实现）。

接口均为百度网盘网页端公开接口，社区工具（BaiduPCS 系列）广泛使用，端点/参数为
接口事实（调研见 PanKeeper-vue3/docs/research/bdsavepro.md）：
- GET /api/quota?checkfree=1&checkexpire=1      容量（total/used 字节），兼做 Cookie 探活
- GET /rest/2.0/xpan/nas?method=uinfo           用户信息（is_vip / vip_type 等）
- GET /rest/2.0/xpan/file?method=list&web=1     真实网盘目录（绕开放平台 /apps 限制）
转存链路（M3）只需 5 个接口：
- POST /share/verify                            提取码验证（Set-Cookie: BDCLND）
- GET  /s/<surl>                                分享页 HTML 抓 yunData（shareid/uk/bdstoken/file_list）
- GET  /share/list                              分享内子目录清单（翻页）
- POST /share/transfer                          转存（fsidlist 一组，Referer 必须是分享链接）
- POST /rest/2.0/xpan/file?method=filemanager   重命名（oper=rename）
解析全部做防御性处理：字段缺失返回 None，不让摘要挂掉。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

import httpx

from ..security import decrypt_credential
from ..services import reqstat
from .base import AdapterError, CloudAdapter, CredentialExpired, ShareBanned, ShareFile, TaskSpec, TransferResult
from .rate_gate import RateGate

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# 同 quark：httpx.Client() 在 Windows 上构造 ~0.5s（SSL 上下文），而 adapter 每个
# 请求都会新建 —— client 必须按账号（Cookie）复用，进程生命周期内不关闭。
_CLIENTS: dict[str, httpx.Client] = {}
_CLIENT_LOCK = threading.Lock()


def _shared_client(cookies: str) -> httpx.Client:
    key = hashlib.sha1(cookies.encode()).hexdigest()
    with _CLIENT_LOCK:
        cli = _CLIENTS.get(key)
        if cli is None:
            cli = httpx.Client(
                headers={"cookie": cookies, "user-agent": UA},
                timeout=15.0,
                # 请求计数（网盘日志页 / 风控预警）：挂在传输层，业务方法零侵入
                event_hooks={"request": [reqstat.hook("baidu")]},
            )
            _CLIENTS[key] = cli
        return cli


class BaiduClient(CloudAdapter):
    type = "baidu"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)
        self.gate = gate or RateGate("baidu", min_interval=1.0, cooldown=30.0)
        self._http = _shared_client(self.cookies)

    def _get(self, url: str, params: dict | None = None) -> dict:
        self.gate.wait()
        try:
            resp = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code in (401, 403):
            raise CredentialExpired("百度 Cookie 已失效")
        try:
            data = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        self.gate.on_success()
        return data

    def verify(self) -> str:
        """quota 兼做探活：errno==0 即 Cookie 有效。"""
        quota = self.quota()
        if quota is None:
            raise CredentialExpired("百度 Cookie 已失效（quota 无数据）")
        try:
            info = self._get("https://pan.baidu.com/rest/2.0/xpan/nas", {"method": "uinfo"})
            if info.get("errno") == 0:
                data = info.get("data") or info
                name = data.get("username") or data.get("baidu_name")
                if name:
                    return str(name)
        except (AdapterError, CredentialExpired):
            pass
        return "百度账号"

    def quota(self) -> dict | None:
        """容量：{total, used} 字节；Cookie 失效时 errno!=0 → None。"""
        data = self._get("https://pan.baidu.com/api/quota", {"checkfree": 1, "checkexpire": 1})
        if data.get("errno") != 0:
            return None
        total, used = data.get("total"), data.get("used")
        if not total:
            return None
        return {"total": int(total), "used": int(used or 0)}

    def vip_info(self) -> dict | None:
        """会员身份：xpan uinfo 的 is_vip / vip_type（公开接口不回到期时间）。"""
        try:
            info = self._get("https://pan.baidu.com/rest/2.0/xpan/nas", {"method": "uinfo"})
        except (AdapterError, CredentialExpired):
            return None
        if info.get("errno") != 0:
            return None
        data = info.get("data") or info
        # 网页 Cookie 下 uinfo 只回 vip_type（is_vip 字段缺失），不能拿 is_vip 做前置判断
        vip_type = int(data.get("vip_type") or 0)
        is_vip = int(data.get("is_vip") or 0)
        if not is_vip and not vip_type:
            return {"name": "普通用户", "expires": None}
        name = {1: "会员", 2: "超级会员", 4: "超级会员"}.get(vip_type, "会员")
        return {"name": name, "expires": None}

    def list_dir(self, directory: str = "/") -> list[dict]:
        """列目录一层（网页端接口，供「默认目标目录」目录树使用）。

        - `web=1` 走 web 通道：拿到的是用户**真实网盘目录**，不受开放平台
          「应用只能访问 /apps/」那条限制（那是 OAuth 接口才有的约束）。
        - 分页：单页最多 1000 条，满页时按 `start` 游标续拉；最多 5 页，
          防止接口异常时无限循环。
        """
        directory = directory if directory.startswith("/") else "/" + directory
        out: list[dict] = []
        start = 0
        for _ in range(5):
            data = self._get(
                "https://pan.baidu.com/rest/2.0/xpan/file",
                {
                    "method": "list",
                    "dir": directory,
                    "web": 1,
                    "order": "name",
                    "desc": 0,
                    "limit": 1000,
                    "start": start,
                },
            )
            errno = data.get("errno")
            if errno == -6:  # 身份验证失败
                raise CredentialExpired("百度 Cookie 已失效")
            if errno not in (0, None):
                raise AdapterError(f"百度列目录失败：errno={errno}")
            page = data.get("list") or []
            out.extend(page)
            if len(page) < 1000:
                break
            start += len(page)
        return out

    def summary(self) -> dict:
        cap, vip = None, None
        try:
            cap = self.quota()
        except (AdapterError, CredentialExpired):
            cap = None
        try:
            vip = self.vip_info()
        except (AdapterError, CredentialExpired):
            vip = None
        return {"capacity": cap, "vip": vip}

    # ---------- 转存链路（M3） ----------
    #
    # 去重顺序照 bdsavepro 实测语义（MD5 优先 → 文件名）；目录树/清单请求走
    # RateGate（1s 最小间隔）。分享链路的请求带 Referer=分享链接（风控要求）。

    _SHARELINK_PREFIX = re.compile(r"^/sharelink\d*-\d+")

    @staticmethod
    def parse_share_url(url: str) -> str:
        """任意形态的分享链接 → surl（/s/xxx 的 xxx）。"""
        url = (url or "").strip().split("#")[0]
        m = re.search(r"/share/init\?surl=([a-zA-Z0-9_-]+)", url)
        if m is None:
            m = re.search(r"/s/1?([a-zA-Z0-9_-]{4,})", url)
        if m is None:
            raise AdapterError(f"无法识别的百度分享链接：{url[:80]}")
        return m.group(1)

    def _share_session(self) -> httpx.Client:
        """分享链路专用会话。

        主客户端的 Cookie 是静态 header（verify/quota/list 足够），但分享验证会
        Set-Cookie: BDCLND，必须用可变 cookie jar——这里按 cookie 串单独建一个，
        解析失败/网盘抖动只影响本次转存，不污染主客户端。
        """
        if getattr(self, "_share_cli", None) is None:
            jar: dict[str, str] = {}
            for part in self.cookies.split(";"):
                if "=" in part:
                    k, _, v = part.strip().partition("=")
                    jar[k] = v
            self._share_cli = httpx.Client(
                cookies=jar,
                headers={"user-agent": UA},
                timeout=20.0,
                follow_redirects=True,
                event_hooks={"request": [reqstat.hook("baidu")]},
            )
        return self._share_cli

    def _share_post(self, url: str, *, params: dict, data: dict, referer: str) -> dict:
        self.gate.wait()
        try:
            resp = self._share_session().post(url, params=params, data=data, headers={"referer": referer})
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

    def _share_get(self, url: str, *, params: dict | None = None, referer: str) -> dict:
        self.gate.wait()
        try:
            resp = self._share_session().get(url, params=params, headers={"referer": referer})
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
    def _check_share_errno(data: dict, action: str) -> None:
        """分享链路错误码分流：死链熔断 / 凭据失效 / 一般失败。"""
        errno = data.get("errno")
        if errno in (0, None):
            return
        if errno in (-7, -8, -9, 115, 145):
            raise ShareBanned(f"errno={errno}（分享已删除/过期/禁止分享）")
        if errno == -6 or errno in (31041, 31042):
            raise CredentialExpired("百度 Cookie 已失效")
        if errno == -12:
            raise AdapterError("提取码错误")
        if errno in (-62, -19):
            raise AdapterError("触发验证码，请稍后再试或手动过一次验证")
        raise AdapterError(f"{action}失败：errno={errno}")

    def _verify_password(self, surl: str, pwd: str) -> None:
        """提取码验证；成功后 BDCLND 落在 share 会话里，后续请求自动携带。"""
        body = self._share_post(
            "https://pan.baidu.com/share/verify",
            params={"surl": surl, "t": int(time.time() * 1000), "channel": "chunlei", "web": 1, "bdstoken": "null", "clienttype": 0},
            data={"pwd": pwd},
            referer=f"https://pan.baidu.com/s/{surl}",
        )
        self._check_share_errno(body, "提取码验证")

    def _fetch_share_page(self, surl: str) -> dict:
        """分享页 HTML 抓 yunData.setData(...) JSON：shareid/uk/bdstoken/file_list。"""
        cli = self._share_session()
        self.gate.wait()
        try:
            resp = cli.get(f"https://pan.baidu.com/s/{surl}", headers={"referer": "https://pan.baidu.com/"})
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        self.gate.on_success()
        html = resp.text
        m = re.search(r"yunData\.setData\((\{.*?\})\)\s*;", html, re.S)
        if m is None:
            if re.search(r"分享的文件已经被取消|分享已过期|分享不存在|链接不存在", html):
                raise ShareBanned("分享页提示链接已失效")
            raise AdapterError("分享页解析失败（未找到 yunData，可能需要先过提取码验证）")
        try:
            return json.loads(m.group(1))
        except ValueError as e:
            raise AdapterError(f"yunData JSON 解析失败：{e}") from e

    def _share_list_dir(self, ctx: dict, dir_path: str) -> list[dict]:
        """分享内子目录清单（/share/list，100/页翻页）。"""
        out: list[dict] = []
        page = 1
        for _ in range(50):  # 5000 项封顶，防异常死循环
            data = self._share_get(
                "https://pan.baidu.com/share/list",
                params={"page": page, "num": 100, "dir": dir_path, "t": int(time.time() * 1000), "uk": ctx["uk"], "shareid": ctx["shareid"], "order": "other", "desc": 1},
                referer=ctx["referer"],
            )
            self._check_share_errno(data, "分享目录清单")
            rows = data.get("list") or []
            out.extend(rows)
            if len(rows) < 100:
                return out
            page += 1
        return out

    @classmethod
    def _share_row_to_file(cls, row: dict, base: str) -> ShareFile:
        """share 接口行 → ShareFile（路径剥 /sharelink 前缀，相对路径从 base 拼起）。"""
        raw_path = cls._SHARELINK_PREFIX.sub("", row.get("path") or "")
        name = row.get("server_filename") or raw_path.rsplit("/", 1)[-1]
        is_dir = bool(row.get("isdir"))
        return ShareFile(
            fid=str(row.get("fs_id") or ""),
            name=name,
            is_dir=is_dir,
            size=int(row.get("size") or 0),
            path=(base + "/" + name).lstrip("/"),
            md5=(row.get("md5") or "").lower(),
        )

    def list_share(self, spec: TaskSpec) -> list[ShareFile]:
        surl = self.parse_share_url(spec.share_url)
        referer = f"https://pan.baidu.com/s/{surl}"
        if spec.share_code:
            self._verify_password(surl, spec.share_code)
        page = self._fetch_share_page(surl)
        ctx = {
            "surl": surl,
            "referer": referer,
            "shareid": page.get("shareid"),
            "uk": page.get("uk") or page.get("share_uk"),
            "bdstoken": page.get("bdstoken") or "",
        }
        if not ctx["shareid"] or not ctx["uk"]:
            raise AdapterError("分享页缺少 shareid/uk（分享可能已失效）")
        self._share_ctx = ctx

        files: list[ShareFile] = []
        roots = [self._share_row_to_file(r, "") for r in (page.get("file_list") or [])]
        files.extend(roots)
        if spec.include_subdirs:
            for node in [f for f in roots if f.is_dir]:
                abs_dir = f"/{node.name}"
                for row in self._share_list_dir(ctx, abs_dir):
                    f = self._share_row_to_file(row, node.name)
                    files.append(f)
                    if f.is_dir:
                        files.extend(self._walk_share_dir(ctx, abs_dir + "/" + f.name, f.path))
        # 排除清单按 basename 过滤（目录也适用——整目录排除）
        if spec.exclude_names:
            files = [f for f in files if f.name not in spec.exclude_names]
        return files

    def _walk_share_dir(self, ctx: dict, abs_dir: str, base: str) -> list[ShareFile]:
        out: list[ShareFile] = []
        for row in self._share_list_dir(ctx, abs_dir):
            f = self._share_row_to_file(row, base)
            out.append(f)
            if f.is_dir:
                out.extend(self._walk_share_dir(ctx, f"{abs_dir}/{f.name}", f.path))
        return out

    def list_dir_names(self, dir_path: str) -> set[str]:
        return {self._basename(e) for e in self.list_dir(dir_path) if not int(e.get("isdir") or 0)}

    def _dir_md5s(self, dir_path: str) -> set[str]:
        out = set()
        for e in self.list_dir(dir_path):
            if not int(e.get("isdir") or 0) and e.get("md5"):
                out.add(str(e["md5"]).lower())
        return out

    @staticmethod
    def _basename(entry: dict) -> str:
        return entry.get("server_filename") or (entry.get("path") or "/").rsplit("/", 1)[-1]

    def _mkdir(self, path: str) -> None:
        """逐级建目录（errno 12 = 已存在，算成功；31062 文件名非法）。"""
        data = self._share_post(
            "https://pan.baidu.com/api/create",
            params={"a": "commit", "web": 1},
            data={"path": path, "isdir": "1", "block_list": "[]"},
            referer="https://pan.baidu.com/disk/main",
        )
        errno = data.get("errno")
        if errno in (0, 12, None):
            return
        if errno == -6 or errno in (31041, 31042):
            raise CredentialExpired("百度 Cookie 已失效")
        if errno == -32:
            raise AdapterError("网盘空间不足")
        raise AdapterError(f"建目录失败 {path}：errno={errno}")

    def _ensure_dirs(self, dirs: list[str]) -> None:
        for d in dirs:
            parts = [p for p in d.strip("/").split("/") if p]
            cur = ""
            for p in parts:
                cur += "/" + p
                self._mkdir(cur)

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        ctx = getattr(self, "_share_ctx", None)
        if not ctx:
            raise AdapterError("分享上下文缺失（save_files 必须跟在 list_share 之后）")
        result = TransferResult()
        save_list = [f for f in files if not f.is_dir and f.fid]

        # 去重（MD5 优先 → 文件名）：对比目录优先 compare_path，空则用 save_dir
        base = spec.compare_path or spec.save_dir
        existing: set[str] = set()
        md5s: set[str] = set()
        if base:
            try:
                existing = self.list_dir_names(base)
                md5s = self._dir_md5s(base)
            except (AdapterError, CredentialExpired) as e:
                on_log(f"对比目录 {base} 读取失败（{e}），本次不做去重基线比对")
        need = []
        for f in save_list:
            if f.md5 and f.md5 in md5s:
                result.skip += 1
            elif (f.target_name or f.name) in existing or f.name in existing:
                result.skip += 1
            else:
                need.append(f)
        on_log(f"清单 {len(save_list)} 项：去重跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

        # 建目录：需要的相对目录全部确保存在（目录条目也在 files 里，直接按 path 收集）
        rel_dirs = sorted({f.path.rsplit("/", 1)[0] for f in need if "/" in f.path})
        if rel_dirs:
            self._ensure_dirs([spec.save_dir.rstrip("/") + "/" + d for d in rel_dirs])

        # 按目标目录分组转存，fsidlist 每组 ≤500（-33 上限 999，留余量）
        done, total = 0, len(need)
        by_dir: dict[str, list[ShareFile]] = {}
        for f in need:
            rel = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            by_dir.setdefault(rel, []).append(f)
        for rel, group in by_dir.items():
            target = (spec.save_dir.rstrip("/") + "/" + rel) if rel else spec.save_dir
            for i in range(0, len(group), 500):
                self._transfer_group(ctx, group[i : i + 500], target, spec, result, on_log)
            done += len(group)
            on_progress(min(99, int(done / total * 100)))
        on_progress(100)
        return result

    def _transfer_group(self, ctx: dict, group: list[ShareFile], target_dir: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        fsids = [int(f.fid) for f in group if str(f.fid).isdigit()]
        if not fsids:
            return
        params = {"shareid": ctx["shareid"], "from": ctx["uk"], "bdstoken": ctx["bdstoken"], "channel": "chunlei", "clienttype": 0, "web": 1, "app_id": 250528}
        data = {"path": target_dir, "fsidlist": json.dumps(fsids)}
        body = self._share_post("https://pan.baidu.com/share/transfer", params=params, data=data, referer=ctx["referer"])
        if body.get("errno") == -65:
            # 频率限制：整组等 10s 重试一次（实测语义）
            on_log("百度提示操作频繁（-65），等待 10 秒后整组重试")
            time.sleep(10)
            body = self._share_post("https://pan.baidu.com/share/transfer", params=params, data=data, referer=ctx["referer"])
        self._check_share_errno(body, "转存提交")
        info = body.get("info") or []
        if info and int(info[0].get("errno") or 0) != 0:
            raise AdapterError(f"转存提交失败：info.errno={info[0].get('errno')}")
        result.add += len(group)
        to_fids = [str(i.get("to_fid") or "") for i in info]
        for idx, f in enumerate(group):
            entry = {"name": f.target_name or f.name, "fid": to_fids[idx] if idx < len(to_fids) else ""}
            result.transferred.append(entry)
        self._apply_renames(group, target_dir, spec, result, on_log)

    def _apply_renames(self, group: list[ShareFile], target_dir: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        """转存后重命名（正则目标名 ≠ 原名时）；filemanager oper=rename，逐条尽力而为。"""
        for f in group:
            target = f.target_name
            if not target or target == f.name:
                continue
            full = target_dir.rstrip("/") + "/" + f.name
            body = self._share_post(
                "https://pan.baidu.com/rest/2.0/xpan/file",
                params={"method": "filemanager", "web": 1},
                data={"filelist": json.dumps([{"path": full, "newname": target}], ensure_ascii=False)},
                referer="https://pan.baidu.com/disk/main",
            )
            if int(body.get("errno") or 0) == 0:
                result.renamed += 1
            else:
                on_log(f"重命名失败 {f.name} → {target}：errno={body.get('errno')}")
