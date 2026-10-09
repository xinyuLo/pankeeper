from __future__ import annotations

import hashlib
import random
import re
import threading
import time
from datetime import datetime

import httpx

from ..security import decrypt_credential
from ..services import reqstat
from ..services.dircache import dir_cache
from .base import (
    AdapterError,
    CloudAdapter,
    CredentialExpired,
    ShareBanned,
    ShareFile,
    TaskSpec,
    TransferResult,
)
from .rate_gate import CircuitOpen, RateGate, RateLimited

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"
)

_RE_SHARE = re.compile(r"(?:pan\.)?quark\.cn/s/(\w+)")
_RE_PWD = re.compile(r"[?&]pwd=([a-zA-Z0-9]+)")
_RE_PDIR = re.compile(r"#/list/share/(\w{32})")

BATCH = 100

def extract_share(url: str) -> dict:
    m = _RE_SHARE.search(url)
    if not m:
        raise AdapterError(f"无法识别的夸克分享链接：{url}")
    out = {"pwd_id": m.group(1), "passcode": "", "pdir_fid": "0"}
    mp = _RE_PWD.search(url)
    if mp:
        out["passcode"] = mp.group(1)
    md = _RE_PDIR.search(url)
    if md:
        out["pdir_fid"] = md.group(1)
    return out

_CLIENTS: dict[str, httpx.Client] = {}
_CLIENT_LOCK = threading.Lock()

def _shared_client(cookies: str) -> httpx.Client:
    key = hashlib.sha1(cookies.encode()).hexdigest()
    with _CLIENT_LOCK:
        cli = _CLIENTS.get(key)
        if cli is None:
            cli = httpx.Client(
                headers={"cookie": cookies, "content-type": "application/json", "user-agent": UA},
                timeout=20.0,

                event_hooks={"request": [reqstat.hook("quark")]},
            )
            _CLIENTS[key] = cli
        return cli

class QuarkAdapter(CloudAdapter):
    type = "quark"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)
        self.gate = gate or RateGate("quark", min_interval=0.8, cooldown=30.0)
        self._stoken = ""
        self._http = _shared_client(self.cookies)

    def _req(self, method: str, url: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
        self.gate.wait()
        params = dict(params or {})
        if "drive" in url:
            params.setdefault("pr", "ucpro")
            params.setdefault("fr", "pc")
        try:
            resp = self._http.request(method, url, params=params, json=json_body)
        except httpx.HTTPError as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code == 401:
            self.gate.on_failure()
            raise CredentialExpired("夸克 Cookie 已失效（401）")
        try:
            data = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e

        if data.get("code") not in (0, None) and resp.status_code != 200:
            self.gate.on_failure()
            raise AdapterError(f"夸克接口错误：{data.get('message') or data.get('status')}")
        self.gate.on_success()
        return data

    @staticmethod
    def _fake_browse_params() -> dict:
        return {"__dt": int(random.uniform(1, 5) * 60 * 1000), "__t": int(time.time() * 1000)}

    def verify(self) -> str:
        self.gate.wait()
        try:
            resp = self._http.get("https://pan.quark.cn/account/info", params={"fr": "pc", "platform": "pc"})
        except httpx.HTTPError as e:
            raise AdapterError(f"网络异常：{e}") from e
        if resp.status_code in (401, 403):
            raise CredentialExpired("夸克 Cookie 已失效")
        data = resp.json()
        if not data.get("data", {}).get("nickname"):
            raise CredentialExpired("夸克 Cookie 已失效（无昵称）")
        self.gate.on_success()
        return data["data"]["nickname"]

    def get_stoken(self, pwd_id: str, passcode: str) -> str:
        data = self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
            json_body={"pwd_id": pwd_id, "passcode": passcode},
        )

        if data.get("status") == 500:
            raise AdapterError("夸克接口网络异常（500）")
        if data.get("status") != 200:
            raise ShareBanned(data.get("message") or "分享已失效")
        stoken = (data.get("data") or {}).get("stoken")
        if not stoken:
            raise AdapterError("未取到 stoken")
        return stoken

    def list_share(self, spec: TaskSpec) -> list[ShareFile]:
        parsed = self.prepare(spec)
        stoken = self._stoken

        top = self._walk_share(stoken, parsed["pwd_id"], parsed["pdir_fid"], False, depth=0)
        if parsed["pdir_fid"] == "0" and len(top) == 1 and top[0].is_dir:
            self._root_shell = top[0]

            files = self._walk_share(stoken, parsed["pwd_id"], top[0].fid, spec.include_subdirs, depth=1, base="/")
        else:
            self._root_shell = None
            files = list(top)
            if spec.include_subdirs:
                for f in [x for x in top if x.is_dir]:
                    files.extend(self._walk_share(stoken, parsed["pwd_id"], f.fid, True, depth=1, base=f.path))

        for f in files:
            f.target_name = spec.rename_map.get(f.name)
        if spec.exclude_names or spec.exclude_md5s:
            files = [
                f
                for f in files
                if f.name not in spec.exclude_names and not (not f.is_dir and f.md5 and f.md5 in spec.exclude_md5s)
            ]
        return files

    def probe_share(self, spec: TaskSpec) -> list[ShareFile]:
        parsed = self.prepare(spec)
        return self._walk_share(self._stoken, parsed["pwd_id"], parsed["pdir_fid"], False, depth=0, base="/", max_pages=1, page_size=10)

    def _walk_share(self, stoken: str, pwd_id: str, pdir_fid: str, include_subdirs: bool, depth: int, base: str = "/", max_pages: int | None = None, page_size: int = 50) -> list[ShareFile]:
        out: list[ShareFile] = []
        page = 1
        while max_pages is None or page <= max_pages:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail",
                params={
                    "pwd_id": pwd_id,
                    "stoken": stoken,
                    "pdir_fid": pdir_fid,
                    "_page": page,
                    "_size": page_size,
                    "_fetch_total": 1,
                    "ver": 2,
                },
            )
            d = data.get("data") or {}
            items = d.get("list") or []
            for it in items:
                name = str(it.get("file_name", ""))
                out.append(
                    ShareFile(
                        fid=str(it.get("fid", "")),
                        fid_token=str(it.get("share_fid_token", "")),
                        name=name,
                        is_dir=bool(it.get("dir")),
                        size=int(it.get("size") or 0),
                        path=(base.rstrip("/") + "/" + name) if base != "/" else "/" + name,
                    )
                )
            total = (d.get("metadata") or {}).get("_total")
            if not items or (total is not None and len(out) >= int(total)) or len(items) < page_size:
                break
            page += 1
        if include_subdirs and depth < 8:
            for f in [x for x in out if x.is_dir]:
                out.extend(self._walk_share(stoken, pwd_id, f.fid, True, depth + 1, base=f.path))
        return out

    def ensure_dir(self, dir_path: str) -> str:
        fid = "0"
        for part in [p for p in dir_path.strip("/").split("/") if p]:
            children = self._list_dir(fid)
            hit = next((c for c in children if c["file_name"] == part and c.get("dir")), None)
            if hit:
                fid = str(hit["fid"])
                continue
            data = self._req(
                "POST",
                "https://drive-pc.quark.cn/1/clouddrive/file",
                json_body={"pdir_fid": fid, "file_name": part, "dir_path": "", "dir_init_lock": False},
            )
            new_fid = (data.get("data") or {}).get("fid")
            if not new_fid:
                raise AdapterError(f"建目录失败：{part}")
            fid = str(new_fid)
        return fid

    def _list_dir(self, pdir_fid: str) -> list[dict]:
        data = self._req(
            "GET",
            "https://drive-pc.quark.cn/1/clouddrive/file/sort",
            params={
                "pdir_fid": pdir_fid,
                "_size": 50,
                "_fetch_total": 1,
                "fetch_all_file": 1,
                "fetch_risk_file_name": 1,
            },
        )
        return (data.get("data") or {}).get("list") or []

    def list_dir_names(self, dir_path: str) -> set[str]:
        fid = self.ensure_dir(dir_path)
        return {str(it.get("file_name")) for it in self._list_dir(fid)}

    def _path_to_fid(self, dir_path: str) -> str:
        return self.ensure_dir(dir_path)

    def create_dir(self, parent_fid: str, name: str) -> str:
        data = self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/file",
            json_body={"pdir_fid": parent_fid, "file_name": name, "dir_path": "", "dir_init_lock": False},
        )
        fid = (data.get("data") or {}).get("fid")
        if not fid:
            raise AdapterError(f"建目录失败：{name}")
        return str(fid)

    def rename_dir(self, fid: str, new_name: str) -> None:
        self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/file/rename",
            json_body={"fid": fid, "file_name": new_name},
        )

    def delete_dir(self, fid: str) -> None:
        self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/file/delete",
            json_body={"action_type": 2, "filelist": [fid], "exclude_fids": []},
        )

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        result = TransferResult()

        if spec.with_shell:
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

        existing = self.list_dir_names(spec.save_dir)
        to_fid = self._path_to_fid(spec.save_dir)
        need = []
        for f in save_list:
            target = f.target_name or f.name
            if target in existing or f.name in existing:
                result.skip += 1
            else:
                need.append(f)
        on_log(f"清单 {len(save_list)} 项：跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

        done = 0
        total = len(need)
        for i in range(0, total, BATCH):
            batch = need[i : i + BATCH]
            self._save_batch(batch, to_fid, spec, result, on_log)
            done += len(batch)
            on_progress(min(99, int(done / total * 100)))
        on_progress(100)
        self._patch_target_cache(spec, to_fid, result)
        return result

    def _save_with_shell(self, spec: TaskSpec, shell: ShareFile | None, files: list[ShareFile], result: TransferResult, on_progress, on_log) -> TransferResult:
        rename = (spec.folder_rename or "").strip()
        shell_name = (
            rename
            or (spec.share_name or "").strip()
            or (shell.name if shell is not None else "")
            or "分享资源"
        )
        if shell_name in self.list_dir_names(spec.save_dir):
            result.skip += 1
            on_log(f"目标已存在同名文件夹「{shell_name}」，跳过转存")
            on_progress(100)
            return result

        if shell is None:
            roots_dirs = [f for f in files if f.is_dir and f.fid and f.path.count("/") <= 1]
            if len(roots_dirs) == 1:
                on_log(f"分享根层为「{roots_dirs[0].name}」+散文件：剥原壳，内容进新壳「{shell_name}」")
            else:
                on_log(f"分享无根文件夹（或多文件夹混杂），已建壳「{shell_name}」承接全部内容")
        on_log(f"在 {spec.save_dir.rstrip('/')} 下新建文件夹「{shell_name}」，剥壳转存分享内容")

        inner = [f for f in files if f.fid and f.fid_token and not f.is_dir]

        if spec.only_paths:
            def _sel(f: ShareFile) -> bool:
                rel = f.path.strip("/")
                for sel in spec.only_paths:
                    sel = sel.strip("/")
                    if rel == sel or rel.startswith(sel + "/"):
                        return True
                return False
            inner = [f for f in inner if _sel(f)]
        used: set[str] = set()
        for f in inner:
            base = f.target_name or f.name
            key = base.lower()
            if key in used:
                parts = f.path.strip("/").rsplit("/", 1)
                parent = parts[0].rsplit("/", 1)[-1] if len(parts) == 2 and parts[0] else ""
                cand = f"{parent}_{base}" if parent else base
                n = 1
                while cand.lower() in used:
                    cand = f"{parent}_{n}_{base}" if parent else f"{n}_{base}"
                    n += 1
                f.target_name = cand
                on_log(f"平铺重名：「{f.path}」→「{cand}」")
                used.add(cand.lower())
            else:
                used.add(key)
        target = spec.save_dir.rstrip("/") + "/" + shell_name
        target_fid = self.ensure_dir(target)
        for i in range(0, len(inner), BATCH):
            self._save_batch(inner[i : i + BATCH], target_fid, spec, result, on_log)
        on_progress(100)
        return result

    def _patch_target_cache(self, spec: TaskSpec, to_fid: str, result: TransferResult) -> None:
        entries = [
            {"fid": t["fid"], "name": t["name"], "is_dir": False, "size": 0}
            for t in result.transferred
            if t.get("fid")
        ]
        if not entries:
            return

        def _append(data):
            have = {d.get("name") for d in data if isinstance(d, dict)}
            return list(data) + [e for e in entries if e["name"] not in have]

        dir_cache.update(("quark", "main", to_fid), _append)

    def _save_batch(self, batch: list[ShareFile], to_fid: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        data = self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/save",
            params=self._fake_browse_params(),
            json_body={
                "fid_list": [f.fid for f in batch],
                "fid_token_list": [f.fid_token for f in batch],
                "to_pdir_fid": to_fid,
                "pwd_id": extract_share(spec.share_url)["pwd_id"],
                "stoken": self._stoken,
                "pdir_fid": "0",
                "scene": "link",
            },
        )
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise AdapterError(f"转存提交失败：{data.get('message')}")
        top_fids = self._poll_task(task_id)

        for idx, f in enumerate(batch):
            result.add += 1
            entry = {"name": f.target_name or f.name, "fid": ""}
            if idx < len(top_fids):
                entry["fid"] = str(top_fids[idx])
            result.transferred.append(entry)
        if spec.rename_map:
            self._apply_renames(batch, top_fids, result, on_log)

    def _poll_task(self, task_id: str, timeout: float = 120.0) -> list:
        deadline = time.time() + timeout
        retry = 0
        while time.time() < deadline:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/task",
                params={"task_id": task_id, "retry_index": retry, **self._fake_browse_params()},
            )
            d = data.get("data") or {}
            status = d.get("status")
            if status == 2:
                return ((d.get("save_as") or {}).get("save_as_top_fids")) or []
            retry += 1
            time.sleep(0.5)
        raise AdapterError(f"转存任务轮询超时：{task_id}")

    def _apply_renames(self, batch: list[ShareFile], top_fids: list, result: TransferResult, on_log) -> None:
        for idx, f in enumerate(batch):
            target = f.target_name
            if not target or target == f.name or idx >= len(top_fids):
                continue
            try:
                self._req(
                    "POST",
                    "https://drive-pc.quark.cn/1/clouddrive/file/rename",
                    json_body={"fid": str(top_fids[idx]), "file_name": target},
                )
                result.renamed += 1
            except (AdapterError, CircuitOpen, RateLimited) as e:
                on_log(f"改名失败 {f.name} → {target}：{e}")

    def summary(self) -> dict:
        member, cap = None, None
        try:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/member",
                params=self._fake_browse_params(),
            )
            d = data.get("data") or {}
            mtype = str(d.get("member_type") or "NORMAL")

            name = {
                "SUPER_VIP": "超级会员",
                "SVIP": "超级会员",
                "VIP": "会员",
                "EXP_SVIP": "体验会员",
                "EXP_VIP": "体验会员",
            }.get(mtype, "普通用户" if mtype == "NORMAL" else mtype)

            exp_raw = {
                "SUPER_VIP": d.get("super_vip_exp_at"),
                "SVIP": d.get("super_vip_exp_at"),
                "EXP_SVIP": d.get("exp_svip_exp_at"),
            }.get(mtype) or d.get("exp_at")
            expires = None
            if exp_raw:
                try:
                    expires = datetime.fromtimestamp(int(exp_raw) / 1000).strftime("%Y-%m-%d")
                except (ValueError, OSError, OverflowError):
                    expires = None
            member = {"name": name, "expires": expires}

            total = d.get("total_capacity")
            used = d.get("use_capacity")
            if total:
                cap = {"total": int(total), "used": int(used or 0)}
        except (AdapterError, CredentialExpired, CircuitOpen):
            member = None

        if cap is None:
            try:
                data = self._req(
                    "GET",
                    "https://drive-pc.quark.cn/1/clouddrive/capacity",
                    params=self._fake_browse_params(),
                )
                d = data.get("data") or {}
                total = d.get("total_capacity")
                if not total:

                    total = sum(v for k, v in d.items() if isinstance(v, (int, float)) and k.startswith(("total", "use")))
                used = d.get("use_capacity") or d.get("used_capacity")
                if total:
                    cap = {"total": int(total), "used": int(used or 0)}
            except (AdapterError, CredentialExpired, CircuitOpen):
                cap = None
        return {"capacity": cap, "vip": member}

    def prepare(self, spec: TaskSpec) -> dict:
        parsed = extract_share(spec.share_url)
        self._stoken = self.get_stoken(parsed["pwd_id"], parsed["passcode"])
        return parsed
