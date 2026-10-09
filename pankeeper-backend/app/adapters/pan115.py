from __future__ import annotations

import hashlib
import re
import threading

import httpx

from ..security import decrypt_credential
from ..services import reqstat
from .base import AdapterError, CloudAdapter, CredentialExpired, RiskControlError, ShareBanned, ShareFile, TaskSpec, TransferResult
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
    m = SHARE_RE.search(url or "")
    if m is None:
        raise AdapterError(f"无法识别的 115 分享链接（需形如 115.com/s/xxx?password=yyy）：{(url or '')[:80]}")
    return m.group(1), m.group(2) or ""

class Pan115Adapter(CloudAdapter):
    type = "115"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)

        self.gate = gate or RateGate("115", min_interval=2.0, cooldown=60.0)
        self._http = _shared_client(self.cookies)
        self._uid: str | None = None

    def _get(self, url: str, params: dict | None = None) -> dict:
        self.gate.wait()
        try:
            resp = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code in (301, 302, 307, 308):

            self.gate.on_failure()
            raise RiskControlError("115 触发风控（HTTP 302 跳转），请暂停 10-30 分钟再试，期间勿反复点检测/转存")
        if resp.status_code == 406:

            self.gate.on_failure()
            raise RiskControlError("115 限频（HTTP 406），请稍后再试")
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
        if resp.status_code in (301, 302, 307, 308):
            self.gate.on_failure()
            raise RiskControlError("115 触发风控（HTTP 302 跳转），请暂停 10-30 分钟再试，期间勿反复点检测/转存")
        try:
            body = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        self.gate.on_success()
        return body

    @staticmethod
    def _check_state(data: dict, action: str, *, already_ok: bool = False) -> bool:
        if data.get("state", True):
            return False
        err = str(data.get("error") or data.get("error_msg") or "")
        if already_ok and ALREADY in err:
            return True
        if "验证码" in err:
            raise RiskControlError(f"115 已触发验证码（疑似风控），停止操作并告警：{err}")
        if "登录" in err or "身份" in err or "cookie" in err.lower():
            raise CredentialExpired(f"115 Cookie 已失效：{err}")
        raise AdapterError(f"{action}失败：{err or data}")

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

        roots_raw = self._snap_page(share_code, receive_code, cid="")
        roots = [self._row_to_file(r, "") for r in roots_raw]

        self._root_shell = roots[0] if (spec.include_subdirs and len(roots) == 1 and roots[0].is_dir) else None
        strip_root = len(roots) == 1 and roots[0].is_dir
        out = list(roots)
        if spec.include_subdirs:
            for node in [f for f in roots if f.is_dir]:
                base = "" if strip_root else node.name
                out.extend(self._walk_share_dir(share_code, receive_code, node.fid, base))
        if spec.exclude_names or spec.exclude_md5s:
            out = [
                f
                for f in out
                if f.name not in spec.exclude_names and not (not f.is_dir and f.md5 and f.md5 in spec.exclude_md5s)
            ]

        self._share_ctx = {"share_code": share_code, "receive_code": receive_code}
        return out

    def probe_share(self, spec: TaskSpec) -> list[ShareFile]:
        share_code, receive_code = parse_share_url(spec.share_url)
        rows = self._snap_page(share_code, receive_code, cid="", limit=20, max_pages=1)
        return [self._row_to_file(r, "") for r in rows]

    def _snap_page(self, share_code: str, receive_code: str, cid: str, limit: int | None = None, max_pages: int = 100) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            params = {"share_code": share_code, "receive_code": receive_code, "offset": offset, "limit": limit or PAGE}
            if cid:
                params["cid"] = cid
            data = self._get("https://webapi.115.com/share/snap", params)
            self._check_state(data, "分享清单")
            d = data.get("data")
            if isinstance(d, dict):
                page = d.get("list") or []
                count = int(d.get("count") or 0)
            else:
                page = data.get("list") or []
                count = int(data.get("count") or 0)
            rows.extend(page)
            offset += len(page)
            if not page or (count and offset >= count):
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
        return bool(row.get("sha") or row.get("sha1"))

    @staticmethod
    def _row_to_file(row: dict, base: str) -> ShareFile:
        name = Pan115Adapter._row_name(row)
        is_dir = not Pan115Adapter._is_file_row(row)
        fid = str(row.get("fid") or row.get("cid") or "")
        return ShareFile(fid=fid, name=name, is_dir=is_dir, size=int(row.get("s") or 0),
                         path=(base + "/" + name).lstrip("/") if name else base, md5="")

    def list_dir_names(self, dir_path: str) -> set[str]:
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
            raw = data.get("data")
            page = raw if isinstance(raw, list) else ((raw or {}).get("list") or [])
            rows.extend(page)
            count = int(data.get("count") or 0)
            offset += len(page)
            if not page or offset >= count:
                return rows
        return rows

    @staticmethod
    def _is_dir_row(row: dict) -> bool:
        return not (row.get("sha") or row.get("sha1"))

    @staticmethod
    def _row_id(row: dict) -> str:
        return str(row.get("cid") or row.get("fid") or "")

    def path_to_cid(self, dir_path: str) -> str:
        cid = "0"
        for seg in [p for p in (dir_path or "").strip("/").split("/") if p]:
            rows = self._list_own_dir(cid)
            match = next(
                (r for r in rows if self._row_name(r) == seg and self._is_dir_row(r)),
                None,
            )
            if match is None:
                raise AdapterError(f"115 目录不存在：{dir_path}（缺 {seg}）")
            cid = self._row_id(match)
        return cid

    def _mkdir(self, parent_cid: str, name: str) -> str:

        body = self._post("https://webapi.115.com/files/add", {"pid": parent_cid, "cname": name})
        self._check_state(body, "建目录")
        new_cid = str((body.get("data") or {}).get("file_id") or (body.get("data") or {}).get("cid") or "")
        if new_cid:
            return new_cid
        for r in self._list_own_dir(parent_cid):
            if self._row_name(r) == name and self._is_dir_row(r):
                return self._row_id(r)
        raise AdapterError(f"建目录后找不到 cid：{name}")

    def ensure_dir(self, dir_path: str) -> str:
        cid = "0"
        for seg in [p for p in (dir_path or "").strip("/").split("/") if p]:
            rows = self._list_own_dir(cid)
            match = next(
                (r for r in rows if self._row_name(r) == seg and self._is_dir_row(r)),
                None,
            )
            if match:
                cid = self._row_id(match)
            else:
                cid = self._mkdir(cid, seg)
        return cid

    def create_dir(self, parent_cid: str, name: str) -> str:
        return self._mkdir(parent_cid, name)

    def rename_dir(self, cid: str, new_name: str) -> None:
        body = self._post(
            "https://webapi.115.com/files/batch_rename",
            {"fid": cid, "file_name": new_name, f"files_new_name[{cid}]": new_name},
        )
        self._check_state(body, "重命名")

    def delete_dir(self, cid: str) -> None:
        body = self._post("https://webapi.115.com/rb/delete", {"pid": 0, "fid[0]": cid, "ignore_warn": 1})
        self._check_state(body, "删除")

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        ctx = getattr(self, "_share_ctx", None)
        if not ctx:
            raise AdapterError("分享上下文缺失（save_files 必须跟在 list_share 之后）")
        result = TransferResult()

        if spec.with_shell and not spec.only_paths:
            return self._save_with_shell(spec, getattr(self, "_root_shell", None), files, result, on_progress, on_log)
        save_list = [f for f in files if not f.is_dir and f.fid]

        if spec.only_paths:
            def _kept(f: ShareFile) -> bool:
                rel = f.path.strip("/")
                for sel in spec.only_paths or set():
                    sel = sel.strip("/")
                    if sel and (rel == sel or rel.startswith(sel + "/")):
                        return True
                return False
            before = len(save_list)
            save_list = [f for f in save_list if _kept(f)]
            result.skip += before - len(save_list)

        bases: list[str] = []
        if spec.compare_path:
            bases.append(spec.compare_path)
        if spec.save_dir and spec.save_dir not in bases:
            bases.append(spec.save_dir)
        existing: set[str] = set()
        for base in bases:
            try:
                existing |= self.list_dir_names(base)
            except (AdapterError, CredentialExpired) as e:
                on_log(f"对比目录 {base} 读取失败（{e}），该目录不参与去重基线")
        need = [f for f in save_list if f.name not in existing and (f.target_name or f.name) not in existing]
        result.skip = len(save_list) - len(need)
        on_log(f"清单 {len(save_list)} 项：去重跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

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
            self._receive_group(ctx, group, cid, spec, result, on_log)
            done += len(group)
            on_progress(min(99, int(done / total * 100)))
        on_progress(100)
        return result

    def _save_with_shell(self, spec: TaskSpec, shell: ShareFile | None, files: list[ShareFile], result: TransferResult, on_progress, on_log) -> TransferResult:
        ctx = getattr(self, "_share_ctx", None)
        if not ctx:
            raise AdapterError("分享上下文缺失")
        shell_name = (
            (spec.folder_rename or "").strip()
            or (spec.share_name or "").strip()
            or (shell.name if shell is not None else "")
            or "分享资源"
        )
        parent_cid = self.ensure_dir(spec.save_dir)
        existing_dirs = {self._row_name(r) for r in self._list_own_dir(parent_cid) if self._is_dir_row(r)}
        if shell_name in existing_dirs:
            result.skip += 1
            on_log(f"目标已存在同名文件夹「{shell_name}」，跳过转存")
            on_progress(100)
            return result

        if shell is None:
            roots_dirs = [f for f in files if f.is_dir and f.fid and "/" not in f.path]
            if len(roots_dirs) == 1:

                prefix = roots_dirs[0].path + "/"
                for f in files:
                    if not f.is_dir and f.fid and f.path.startswith(prefix):
                        f.path = f.path[len(prefix):]
                on_log(f"分享根层为「{roots_dirs[0].name}」+散文件：剥原壳，内容进新壳「{shell_name}」")
            else:
                on_log(f"分享无根文件夹（或多文件夹混杂），已建壳「{shell_name}」承接全部内容")
        on_log(f"在 {spec.save_dir.rstrip('/')} 下新建文件夹「{shell_name}」，剥壳转存分享内容")

        target_cid = self.ensure_dir(spec.save_dir.rstrip("/") + "/" + shell_name)

        def _ensure_under(base_cid: str, rel: str) -> str:

            cid = base_cid
            for seg in [p for p in rel.split("/") if p]:
                rows = self._list_own_dir(cid)
                match = next(
                    (r for r in rows if self._row_name(r) == seg and self._is_dir_row(r)),
                    None,
                )
                cid = self._row_id(match) if match else self._mkdir(cid, seg)
            return cid

        use_whole_dir = not spec.rename_map and not spec.only_paths
        top_dirs = [
            f for f in files if f.is_dir and f.fid and "/" not in f.path and f is not shell
        ] if use_whole_dir else []
        loose = [f for f in files if not f.is_dir and f.fid and "/" not in f.path]
        deeper = [f for f in files if not f.is_dir and f.fid and "/" in f.path]
        if spec.only_paths:
            def _sel_path(path: str) -> bool:
                rel = path.strip("/")
                return any(rel == sel.strip("/") or rel.startswith(sel.strip("/") + "/") for sel in spec.only_paths)
            loose = [f for f in loose if _sel_path(f.path)]
            deeper = [f for f in deeper if _sel_path(f.path)]
        if shell is not None:
            deeper = deeper if not use_whole_dir else []
        else:
            roots_dirs = [f for f in files if f.is_dir and f.fid and "/" not in f.path]
            if len(roots_dirs) == 1 and (loose or deeper):

                top_dirs = []
                on_log(f"分享根层为「{roots_dirs[0].name}」+散文件：剥原壳，内容进新壳「{shell_name}」")
            else:
                deeper = deeper if not use_whole_dir else []
                if use_whole_dir:
                    on_log(f"已建壳「{shell_name}」承接全部内容（文件夹整目录接收）")
        on_log(f"在 {spec.save_dir.rstrip('/')} 下新建文件夹「{shell_name}」，剥壳转存分享内容")

        for d in top_dirs:
            inner = [f.name for f in files if not f.is_dir and f.path.startswith(d.path + "/")]
            self._receive_group(ctx, [d], target_cid, spec, result, on_log, add_count=len(inner), extra_names=inner)
        for i in range(0, len(loose), 1000):
            self._receive_group(ctx, loose[i : i + 1000], target_cid, spec, result, on_log)
        by_dir: dict[str, list[ShareFile]] = {}
        for f in deeper:
            by_dir.setdefault(f.path.rsplit("/", 1)[0], []).append(f)
        for rel, group in by_dir.items():
            cid = _ensure_under(target_cid, rel)
            self._receive_group(ctx, group, cid, spec, result, on_log)
        on_progress(100)
        return result

    def _receive_group(self, ctx: dict, files: list[ShareFile], cid: str, spec: TaskSpec, result: TransferResult, on_log,
                       add_count: int | None = None, extra_names: list[str] | None = None) -> None:
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
            result.skip += add_count if add_count is not None else len(files)
            return
        result.add += add_count if add_count is not None else len(files)
        if add_count is None:

            for f in files:
                result.transferred.append({"name": f.target_name or f.name, "fid": f.fid})
        for n in extra_names or []:
            result.transferred.append({"name": n, "fid": ""})
        if add_count is None:
            self._apply_renames(files, cid, spec, result, on_log)

    def _apply_renames(self, group: list[ShareFile], cid: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        if not spec.rename_map:
            return
        rows = self._list_own_dir(cid)
        for f in group:
            target = f.target_name
            if not target or target == f.name:
                continue
            hit = next((r for r in rows if self._row_name(r) == f.name and self._is_file_row(r)), None)
            if hit is None:
                on_log(f"改名跳过 {f.name}：目标目录里没找到刚接收的文件")
                continue
            row_id = str(hit.get("cid") or hit.get("fid") or "")
            body = self._post(
                "https://webapi.115.com/files/batch_rename",
                {"fid": row_id, "file_name": target, f"files_new_name[{row_id}]": target},
            )
            self._check_state(body, "改名")
            result.renamed += 1

    def summary(self) -> dict:
        vip = {"name": "普通用户", "expires": None}
        cap = None
        try:
            data = self._get("https://my.115.com/", {"ct": "ajax", "ac": "get_user_aq"})
            self._check_state(data, "会员信息")
            v = (data.get("data") or {}).get("vip") or {}
            if v.get("is_vip") or v.get("vip"):
                expires = str(v.get("expire_str") or "").strip()
                if expires in ("", "0", "1970-01-01"):
                    expires = None
                vip = {"name": "永久会员" if v.get("is_forever") else "会员", "expires": expires}
        except (AdapterError, CredentialExpired):
            pass
        try:
            d = self._get("https://115.com/index.php", {"ct": "ajax", "ac": "get_storage_info"})
            total = used = 0
            for v in (d if isinstance(d, dict) else {}).values():
                if isinstance(v, dict) and (v.get("total") or v.get("used")):
                    total += int(v.get("total") or 0)
                    used += int(v.get("used") or 0)
            if total:
                cap = {"total": total, "used": used}
        except (AdapterError, CredentialExpired):
            pass
        return {"capacity": cap, "vip": vip}
