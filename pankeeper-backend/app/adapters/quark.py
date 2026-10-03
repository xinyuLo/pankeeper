"""夸克网盘适配器（原创实现）。

接口事实依据：docs/research/quark-auto-save.md §2（端点/参数/批量上限/状态码语义）。
设计要点：
- httpx 同步 Client，每账号一个实例；所有请求过 RateGate（0.8s 间隔）；
- stoken 接口兼分享死活探测：HTTP 200 正常 / 500 网络异常重试 / 其他状态 = 分享失效（ShareBanned 熔断）；
- 转存 100 个/批（save_as_top_fids 硬上限），task 轮询 0.5s；
- 去重按目标名（正则改名后的名字也参与比对）。
"""
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

BATCH = 100  # 服务端 save_as_top_fids 上限 100，超过会被截断


def extract_share(url: str) -> dict:
    """从分享链接解析 pwd_id / 提取码 / 子目录 fid（无需请求网页）。"""
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


# httpx.Client() 在 Windows 上构造一次 ~0.5s（SSL 上下文加载），而 adapter 每个
# 请求都会新建 —— client 必须按账号（Cookie）复用，进程生命周期内不关闭。
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
                # 请求计数（网盘日志页 / 风控预警）：挂在传输层，业务方法零侵入
                event_hooks={"request": [reqstat.hook("quark")]},
            )
            _CLIENTS[key] = cli
        return cli


class QuarkAdapter(CloudAdapter):
    type = "quark"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)
        self.gate = gate or RateGate("quark", min_interval=0.8, cooldown=30.0)
        self._stoken = ""  # list_share 取一次，save 批次复用
        self._http = _shared_client(self.cookies)

    # ---------- 基础请求 ----------

    def _req(self, method: str, url: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
        self.gate.wait()
        params = dict(params or {})
        if "drive" in url:  # drive-pc.quark.cn 的公共参数
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
        # 夸克业务码：code==0 成功；status 为 HTTP 层语义
        if data.get("code") not in (0, None) and resp.status_code != 200:
            self.gate.on_failure()
            raise AdapterError(f"夸克接口错误：{data.get('message') or data.get('status')}")
        self.gate.on_success()
        return data

    @staticmethod
    def _fake_browse_params() -> dict:
        """伪装页面停留时长的行为参数（1~5 分钟的毫秒数 + 当前时间戳）。"""
        return {"__dt": int(random.uniform(1, 5) * 60 * 1000), "__t": int(time.time() * 1000)}

    # ---------- 凭据 ----------

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

    # ---------- 分享 ----------

    def get_stoken(self, pwd_id: str, passcode: str) -> str:
        data = self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
            json_body={"pwd_id": pwd_id, "passcode": passcode},
        )
        # 业务语义：status!=200 且非 500 → 分享失效（500 视为网络抖动走 AdapterError 重试链）
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
        # ⚠️ 单壳判定必须只看**根层条目**（2026-10-04 用户套娃案根因）：先只列根层再判定。
        # 原实现拿 `_walk_share(include_subdirs=True)` 的 len(files) 判定——那个列表含壳内
        # 全部文件，壳里有文件就永远 >1 → 标准单壳被误判成"非单壳" → 建壳 + 原壳整转 → 双层同名。
        top = self._walk_share(stoken, parsed["pwd_id"], parsed["pdir_fid"], False, depth=0)
        if parsed["pdir_fid"] == "0" and len(top) == 1 and top[0].is_dir:
            self._root_shell = top[0]  # 「带壳转存」用：壳条目（fid/fid_token 可整体转存）
            # 剥壳：以壳为根遍历，相对路径不含壳名（base="/"）
            files = self._walk_share(stoken, parsed["pwd_id"], top[0].fid, spec.include_subdirs, depth=1, base="/")
        else:
            self._root_shell = None
            files = list(top)
            if spec.include_subdirs:
                for f in [x for x in top if x.is_dir]:
                    files.extend(self._walk_share(stoken, parsed["pwd_id"], f.fid, True, depth=1, base=f.path))
        # 排除清单 + 改名映射
        for f in files:
            f.target_name = spec.rename_map.get(f.name)
        if spec.exclude_names or spec.exclude_md5s:
            files = [
                f
                for f in files
                if f.name not in spec.exclude_names and not (not f.is_dir and f.md5 and f.md5 in spec.exclude_md5s)
            ]
        return files

    def _walk_share(self, stoken: str, pwd_id: str, pdir_fid: str, include_subdirs: bool, depth: int, base: str = "/") -> list[ShareFile]:
        out: list[ShareFile] = []
        page = 1
        while True:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail",
                params={
                    "pwd_id": pwd_id,
                    "stoken": stoken,
                    "pdir_fid": pdir_fid,
                    "_page": page,
                    "_size": 50,
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
            if not items or (total is not None and len(out) >= int(total)) or len(items) < 50:
                break
            page += 1
        if include_subdirs and depth < 8:
            for f in [x for x in out if x.is_dir]:
                out.extend(self._walk_share(stoken, pwd_id, f.fid, True, depth + 1, base=f.path))
        return out

    # ---------- 目标盘操作 ----------

    def ensure_dir(self, dir_path: str) -> str:
        """逐级建目录，返回最末级 fid；已存在视为成功。

        ⚠️ 逐层匹配必须用**当前层 fid** 列目录再按名字找。曾把绝对路径当 fid 传给
        _list_dir（永远查空 → 每层都误走创建），而创建接口的 dir_path 是相对
        pdir_fid 的——绝对路径塞进去就把 /1.影视/待整理-电影 建成了
        /1.影视/1.影视/待整理-电影（2026-10-03 套娃目录案）。"""
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
        """列自己的目录（pdir_fid 支持 fid 或路径；这里传 fid）。"""
        data = self._req(
            "GET",
            "https://drive-pc.quark.cn/1/clouddrive/file/sort",
            params={
                "pdir_fid": pdir_fid,
                "_size": 50,
                "_fetch_total": 1,
                "fetch_all_file": 1,
                "fetch_risk_file_name": 1,  # 不带此参数违规文件名会返回 ***，影响去重
            },
        )
        return (data.get("data") or {}).get("list") or []

    def list_dir_names(self, dir_path: str) -> set[str]:
        fid = self.ensure_dir(dir_path)  # 不存在会顺带建好，语义对转存更顺
        return {str(it.get("file_name")) for it in self._list_dir(fid)}

    def _path_to_fid(self, dir_path: str) -> str:
        """路径 → fid（建好目录树后取末级）。"""
        return self.ensure_dir(dir_path)

    # ---------- 目录管理（浏览弹窗的新建/重命名/删除，2026-10-03） ----------

    def create_dir(self, parent_fid: str, name: str) -> str:
        """在指定父目录下建文件夹，返回新 fid。与 ensure_dir 的逐层建同款接口。"""
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
        """删除目录（action_type=2 = 文件+文件夹通用；夸克目录删除是递归的）。"""
        self._req(
            "POST",
            "https://drive-pc.quark.cn/1/clouddrive/file/delete",
            json_body={"action_type": 2, "filelist": [fid], "exclude_fids": []},
        )

    # ---------- 转存 ----------

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        result = TransferResult()
        # 「带壳转存」（spec.with_shell，搜索转存快速弹窗）：单壳分享整壳转 + 可选根文件夹更名。
        # 早期 return——跳过文件级名字去重（整壳模式按"目标已有同名文件夹"去重）
        if spec.with_shell and not spec.only_paths:
            shell = getattr(self, "_root_shell", None)
            if shell is None or (shell.fid and shell.fid_token):
                return self._save_with_shell(spec, shell, files, result, on_progress, on_log)
        save_list = [f for f in files if not f.is_dir and f.fid]
        # 去重：目标目录已有同名（按目标名比对，改名后的名字也算已存在）
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
        """带壳转存（搜索转存快速弹窗，spec.with_shell）：

        - 单壳（`_root_shell`）：整壳转过来 + `folder_rename` 非空时按转存返回的 fid rename；
        - 非单壳（散文件/多文件夹，2026-10-04 用户："没壳的文件建个壳套进去，多文件也不用管了"）：
          在 save_dir 下建壳（壳名 = 更名值或分享名），根层条目全部转进壳。
        去重口径：目标已有同名文件夹 → 整壳跳过。目录缓存不打补丁（_patch_target_cache 只认
        文件条目）——树展开时按需回源即可。
        """
        rename = (spec.folder_rename or "").strip()
        if shell is not None:
            target = spec.save_dir.rstrip("/") + "/" + shell.name
            shown = shell.name
        else:
            shell_name = rename or (spec.share_name or "").strip() or "分享资源"
            target = spec.save_dir.rstrip("/") + "/" + shell_name
            shown = shell_name
        if shown in self.list_dir_names(spec.save_dir):
            result.skip += 1
            on_log(f"目标已存在同名文件夹「{shown}」，跳过整壳转存")
            on_progress(100)
            return result

        if shell is not None:
            on_log(f"整壳转存分享根文件夹「{shell.name}」→ {spec.save_dir}/")
            to_fid = self._path_to_fid(spec.save_dir)
            self._save_batch([shell], to_fid, spec, result, on_log)
            if rename and rename != shell.name:
                fid = (result.transferred[-1].get("fid") or "") if result.transferred else ""
                time.sleep(1)  # 转存任务刚完就 rename 是写操作连打，歇一拍
                if fid:
                    self.rename_dir(fid, rename)
                    result.renamed += 1
                    if result.transferred:
                        result.transferred[-1]["name"] = rename
                    on_log(f"根文件夹已更名：{shell.name} → {rename}")
                else:
                    on_log(f"根文件夹更名失败：拿不到转存后的 fid（保持原名 {shell.name}）")
        else:
            roots_dirs = [f for f in files if f.is_dir and f.fid and f.path.count("/") <= 1]
            if len(roots_dirs) == 1:
                # 根层 = 1 个文件夹 + N 散文件：剥原壳（同 baidu，防同名套娃）
                prefix = roots_dirs[0].path + "/"
                for f in files:
                    if not f.is_dir and f.fid and f.path.startswith(prefix):
                        f.path = f.path[len(prefix):]
                on_log(f"分享根层为「{roots_dirs[0].name}」+散文件：剥原壳，内容进新壳「{shell_name}」")
            else:
                on_log(f"分享无根文件夹（或多文件夹混杂），已建壳「{shell_name}」承接全部内容")
            # 全部按**文件条目**转（文件夹条目跳过，目录靠 ensure_dir 重建）——防重复转存
            inner = [f for f in files if f.fid and f.fid_token and not f.is_dir]
            by_dir: dict[str, list[ShareFile]] = {}
            for f in inner:
                rel = f.path.lstrip("/").rsplit("/", 1)[0] if "/" in f.path.strip("/") else ""
                by_dir.setdefault(rel, []).append(f)
            base_fid = self._path_to_fid(spec.save_dir)
            target_fid = self.ensure_dir(target)  # 壳：根层散文件（剥前缀后 rel 为空）也必须落进壳里
            for rel, group in by_dir.items():
                tgt_fid = self.ensure_dir(spec.save_dir.rstrip("/") + "/" + rel) if rel else target_fid
                for i in range(0, len(group), BATCH):
                    self._save_batch(group[i : i + BATCH], tgt_fid, spec, result, on_log)
        on_progress(100)
        return result

    def _patch_target_cache(self, spec: TaskSpec, to_fid: str, result: TransferResult) -> None:
        """转存成功后把新增条目就地追加进目标目录的缓存列表（写后不回源）。

        只在目标目录已有缓存时生效（update 对 miss 静默跳过）；拿不到 fid 的
        条目跳过——树展开时反正会按需回源。"""
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
        # top_fids 与提交 fid_list 顺序对齐；对不齐就不做改名（宁可原名也不改错名）
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
        """会员 + 容量摘要（防御性解析，拿不到的字段为 None）。

        接口为夸克网页端公开接口（社区工具广泛使用）：
        - GET /1/clouddrive/member   会员类型与到期时间
        - GET /1/clouddrive/capacity 容量（含各分类，取合计字段）
        """
        member, cap = None, None
        try:
            data = self._req(
                "GET",
                "https://drive-pc.quark.cn/1/clouddrive/member",
                params=self._fake_browse_params(),
            )
            d = data.get("data") or {}
            mtype = str(d.get("member_type") or "NORMAL")
            # 夸克各版本 member_type 取值不一（SUPER_VIP / SVIP / VIP / EXP_SVIP），统一映射中文
            name = {
                "SUPER_VIP": "超级会员",
                "SVIP": "超级会员",
                "VIP": "会员",
                "EXP_SVIP": "体验会员",
                "EXP_VIP": "体验会员",
            }.get(mtype, "普通用户" if mtype == "NORMAL" else mtype)
            # 到期时间：按会员类型取对应字段（均为毫秒时间戳），最后回落到通用 exp_at。
            # 注意夸克并不返回 member_expires 这个字段（早前就是因此一直是 null）。
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
            # 容量同在这个响应里：顺手取走，省掉一次 /capacity 请求（请求密度越低越不易触发风控）
            total = d.get("total_capacity")
            used = d.get("use_capacity")
            if total:
                cap = {"total": int(total), "used": int(used or 0)}
        except (AdapterError, CredentialExpired, CircuitOpen):
            member = None

        # 兜底：member 响应里没带容量时才单独问一次 /capacity
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
                    # 有些版本把合计放在 file_* 分类里，做一次兜底求和
                    total = sum(v for k, v in d.items() if isinstance(v, (int, float)) and k.startswith(("total", "use")))
                used = d.get("use_capacity") or d.get("used_capacity")
                if total:
                    cap = {"total": int(total), "used": int(used or 0)}
            except (AdapterError, CredentialExpired, CircuitOpen):
                cap = None
        return {"capacity": cap, "vip": member}

    # stoken 在 list_share 与 save 之间复用，避免重复请求

    def prepare(self, spec: TaskSpec) -> dict:
        """取 stoken 并缓存（save 批次复用），返回解析结果。"""
        parsed = extract_share(spec.share_url)
        self._stoken = self.get_stoken(parsed["pwd_id"], parsed["passcode"])
        return parsed
