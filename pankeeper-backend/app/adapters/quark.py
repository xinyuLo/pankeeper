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
    ShareLocked,
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

# 去重基线惰性下钻子目录的额外 list 请求上限（每任务共享，风控：宁可漏扫不可连打）
SUBDIR_SCAN_LIMIT = 3


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
    # 目录缓存 key 的账号段：由 make_adapter_for 注入（与 cache_api.list_files 同口径，
    # T-001-F4）；未注入（测试 __new__ 出来的裸实例等）默认 "main"
    acc_key: str = "main"

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
        # 业务语义：status!=200 且非 500 → 分享失效（500 视为网络抖动走 AdapterError 重试链）。
        # 例外：「要提取码/访问码」是活链可行动错误（补码重试），不能混进死链——
        # 2026-10-15 实锤：缺码活链被当 ShareBanned → check-link 判 bad 拦死，
        # 执行期则变「分享清单失败：请输入访问码」失败推送（轰炸源之一）。
        if data.get("status") == 500:
            raise AdapterError("夸克接口网络异常（500）")
        if data.get("status") != 200:
            msg = data.get("message") or "分享已失效"
            if any(k in msg for k in ("提取码", "访问码", "密码")):
                raise ShareLocked(f"夸克分享需要提取码/访问码：{msg}")
            raise ShareBanned(msg)
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

    def probe_share(self, spec: TaskSpec) -> list[ShareFile]:
        """死活探针：prepare + 根层第一页 10 条（翻完整根层是有效链检测慢的主因）。"""
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
        """契约方法：一层现有条目名（含目录名）。内部走 ensure_dir（不存在会顺带
        建好，语义对转存更顺）。递归下钻版见 _dir_baseline——compare_path 只读不建。"""
        fid = self.ensure_dir(dir_path)
        return {str(it.get("file_name")) for it in self._list_dir(fid)}

    def _path_to_fid(self, dir_path: str) -> str:
        """路径 → fid（建好目录树后取末级）。"""
        return self.ensure_dir(dir_path)

    def _resolve_fid_soft(self, dir_path: str) -> str | None:
        """路径 → fid；任一层缺失返回 None（**不建目录**）。

        ensure_dir 会把缺失层级建出来；扫 compare_path（QMS 媒体库）用它会在库里
        凭空建一串空目录——对比路径只允许读（T-001 硬约束）。"""
        fid = "0"
        for part in [p for p in dir_path.strip("/").split("/") if p]:
            hit = next((c for c in self._list_dir(fid) if c.get("file_name") == part and c.get("dir")), None)
            if hit is None:
                return None
            fid = str(hit["fid"])
        return fid

    # 基线扫描的行读取：兼容网盘原始行（file_name/dir）与 dir_cache 归一行（name/is_dir）
    @staticmethod
    def _row_name(it: dict) -> str:
        return str(it.get("file_name") or it.get("name") or "")

    @staticmethod
    def _row_is_dir(it: dict) -> bool:
        if "is_dir" in it:
            return bool(it.get("is_dir"))
        return bool(it.get("dir"))

    def _list_dir_norm(self, fid: str) -> list[dict]:
        """列自己的目录并归一成 dir_cache 统一形状（与 cache_api._load_dir_payload 夸克分支一致）。"""
        return [
            {"fid": str(it.get("fid", "")), "name": str(it.get("file_name", "")), "is_dir": bool(it.get("dir")), "size": int(it.get("size") or 0)}
            for it in self._list_dir(fid)
        ]

    def _dir_baseline(
        self,
        dir_path: str,
        *,
        create: bool = False,
        cand_names: set[str] | None = None,
        budget: list[int] | None = None,
        on_log=None,
        stats: dict | None = None,
    ) -> set[str]:
        """去重基线：目标目录现有条目名（含目录名，沿用 list_dir_names 口径）。

        T-001：① 基线纳入 compare_path（此前 save_files 只扫 save_dir——QMS 把文件
        搬进媒体库「剧名/Season 1/」后，save_dir 里空空如也，同一集每次都判新增）；
        ② 惰性递归一层：先只扫 base 一层，cand_names（本轮仍待比对的名字）全部
        命中就不下钻；仍有未命中才逐个扫直接子目录——子目录清单经 dir_cache 读穿
        （命中缓存不打网盘），额外 list 请求受 budget（[剩余次数]，任务内跨目录
        共享）上限约束，超限立即停。create=False（compare_path）走 _resolve_fid_soft
        绝不建目录；create=True（save_dir）沿用 ensure_dir（转存目标本来就要存在）。
        夸克无 MD5，名字即口径。CredentialExpired 一律上抛。
        """
        fid = self.ensure_dir(dir_path) if create else (self._resolve_fid_soft(dir_path) or "")
        if not fid:
            if on_log:
                on_log(f"对比目录 {dir_path} 不存在，不参与去重基线")
            if stats is not None:
                stats["dirs"] = 0
            return set()
        if budget is None:
            budget = [SUBDIR_SCAN_LIMIT]
        names: set[str] = set()
        subdirs: list[str] = []
        for it in self._list_dir(fid):
            names.add(self._row_name(it))
            if self._row_is_dir(it) and it.get("fid"):
                subdirs.append(str(it["fid"]))
        scanned = 1

        def _pending() -> bool:
            return bool(cand_names) and any(n not in names for n in cand_names)

        for sub in subdirs:
            if not _pending():
                break
            if budget[0] <= 0:
                if on_log:
                    on_log(f"子目录扫描达上限（{SUBDIR_SCAN_LIMIT} 次/任务），因上限停止递归：{dir_path}")
                if stats is not None:
                    stats["stopped"] = True
                break
            flag: dict = {}
            ok = True
            try:
                rows = dir_cache.get_or_load(("quark", self.acc_key, sub), lambda f=sub: self._list_dir_norm(f), flag=flag)
            except CredentialExpired:
                raise
            except (AdapterError, CircuitOpen, RateLimited) as e:
                if on_log:
                    on_log(f"子目录读取失败（{e}），跳过")
                ok = False
                rows = []
            if not flag.get("cached"):
                budget[0] -= 1  # 真打了网盘才占预算（失败请求也算）
            if not ok:
                continue
            scanned += 1  # 列到了就计数（空目录也是扫过的目录）；失败的才不计
            # 只下钻一层：孙目录不进基线（媒体库就两层，深挖纯属给风控送请求）
            names |= {self._row_name(it) for it in rows}
        if stats is not None:
            stats["dirs"] = scanned
        return names

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
        # 「建壳转存」（spec.with_shell，搜索转存快速弹窗）：按资源名/更名值新建文件夹、
        # 剥壳转入。早期 return——跳过文件级名字去重（建壳模式按"目标已有同名文件夹"去重）
        if spec.with_shell:
            return self._save_with_shell(spec, getattr(self, "_root_shell", None), files, result, on_progress, on_log)
        save_list = [f for f in files if not f.is_dir and f.fid]
        # 勾选清单过滤（bdsavePro new_files 语义）：勾了文件=只转这些；勾了目录=整棵子树。
        # ⚠️ 与百度同款，此前漏了会全量转（115 侧 2026-10-04 实锤后同修）
        if spec.only_paths:
            def _kept(f: ShareFile) -> bool:
                rel = f.path.strip("/")  # 两侧同口径剥斜杠——只剥 sel 会让顶层文件永远比不中（2026-10-08 实锤全跳过）
                for sel in spec.only_paths or set():
                    sel = sel.strip("/")
                    if sel and (rel == sel or rel.startswith(sel + "/")):
                        return True
                return False
            before = len(save_list)
            save_list = [f for f in save_list if _kept(f)]
            result.skip += before - len(save_list)
        # 去重：目标目录已有同名（按目标名比对，改名后的名字也算已存在）。
        # 基线 = compare_path ∪ save_dir（T-001：此前只扫 save_dir，QMS 把文件搬进
        # 媒体库后 save_dir 一空，同一集每次都判新增）；两边都惰性递归一层，子目录
        # 经 dir_cache 读穿、额外 list 请求受 SUBDIR_SCAN_LIMIT 上限约束。
        bases: list[tuple[str, bool]] = []
        if spec.compare_path and spec.compare_path != spec.save_dir:
            bases.append((spec.compare_path, False))
        if spec.save_dir:
            # save_dir 基线也只读（T-007）：目录缺失时的补建统一放到转存前那一步——
            # 全部去重跳过的任务不该在网盘里留下空目录（T-001 约束 7 同精神）
            bases.append((spec.save_dir, False))
        existing: set[str] = set()

        def _pending_cands() -> set[str]:
            # 本轮仍待比对的名字（原名+目标名，按最终匹配口径剔已命中）：
            # 下个 base 的惰性递归据此决定值不值得下钻
            left: set[str] = set()
            for f in save_list:
                if (f.target_name or f.name) in existing or f.name in existing:
                    continue
                left.add(f.name)
                if f.target_name:
                    left.add(f.target_name)
            return left

        budget = [SUBDIR_SCAN_LIMIT]
        scan_stats: dict = {"dirs": 0, "stopped": False}
        for base, create in bases:
            # T-001-F2：对齐 baidu/115 语义——凭据失效上抛标记账号；单目录抖动/风控
            # 只跳过该 base 记日志，不再让整单失败（compare_path 进基线后必须有这层兜底）
            try:
                st: dict = {}
                existing |= self._dir_baseline(
                    base, create=create, cand_names=_pending_cands(), budget=budget, on_log=on_log, stats=st
                )
                scan_stats["dirs"] += int(st.get("dirs") or 0)
                scan_stats["stopped"] = scan_stats["stopped"] or bool(st.get("stopped"))
            except CredentialExpired:
                raise
            except (AdapterError, CircuitOpen, RateLimited) as e:
                on_log(f"对比目录 {base} 读取失败（{e}），该目录不参与去重基线")
        on_log(f"去重基线：扫描了 {scan_stats['dirs']} 个目录（含子目录），现有 {len(existing)} 个条目名")
        need = []
        for f in save_list:
            target = f.target_name or f.name
            if target in existing or f.name in existing:
                result.skip += 1
                # 名字闸命中（夸克无 MD5）——名单落库供详情查看；无法比对 MD5（T-017-F2）
                result.name_skipped.append({"name": f.name, "md5_diff": False, "md5_uncomparable": True})
            else:
                need.append(f)
        on_log(f"清单 {len(save_list)} 项：跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

        # T-007：转存前确保目标目录存在——缺失自动建一次并留痕（此前 ensure_dir
        # 静默建、目录被删也无从知晓）；存在则软解析出的 fid 直接复用，省一次走查
        to_fid = self._resolve_fid_soft(spec.save_dir)
        if to_fid is None:
            to_fid = self.ensure_dir(spec.save_dir)
            on_log(f"目标目录 {spec.save_dir} 不存在，已自动创建")

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
        """带壳转存（搜索转存快速弹窗，spec.with_shell）——**建壳承接 + 全平铺**（2026-10-06 用户定稿，同 baidu）：

        直接在 save_dir 下按「更名值或资源名」新建文件夹当壳，分享内容**剥掉所有文件夹
        层级**平铺转进去（用户实锤：壳里套壳的多层文件夹 QMS 识别不出来，一层都不能留）。
        搜索源给的名字是资源名而非分享真实目录名，整壳转过来名字对不上；rename 事后改
        实测不可靠。建壳后资源名/更名值/QMS 记录文件名三者天然一致。
        - 不同子目录的同名文件自动改名（父目录名前缀）防同名覆盖丢文件，日志如实记录；
        - 去重口径：目标已有同名文件夹 → 整单跳过。目录缓存不打补丁（_patch_target_cache 只认
          文件条目）——树展开时按需回源即可。
        """
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
        # 全部按**文件条目**转（文件夹条目跳过）——
        # —— 全平铺：壳内所有子文件夹层级都不留（多层结构 QMS 识别不出，2026-10-06 用户实锤）。
        # 不同子目录的同名文件自动改名（父目录名前缀）防同名覆盖丢文件，日志留痕。
        inner = [f for f in files if f.fid and f.fid_token and not f.is_dir]
        # 勾选清单（弹窗多选，电影+多文件场景）：壳内只转勾选的文件
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
        target_fid = self.ensure_dir(target)  # 壳：全部文件平铺进这一层
        for i in range(0, len(inner), BATCH):
            self._save_batch(inner[i : i + BATCH], target_fid, spec, result, on_log)
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

        dir_cache.update(("quark", self.acc_key, to_fid), _append)  # 账号段随实例（T-001-F4）

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
        # 提取码回落：链接没带 ?pwd= 时用独立 share_code 字段（搜索结果给的提取码），
        # 与百度 list_share「三个来源都认」同口径——此前夸克完全无视 share_code，
        # 密码不在链接里就裸奔（2026-10-15 实锤）
        if not parsed["passcode"] and (spec.share_code or "").strip():
            parsed["passcode"] = spec.share_code.strip()
        self._stoken = self.get_stoken(parsed["pwd_id"], parsed["passcode"])
        return parsed
