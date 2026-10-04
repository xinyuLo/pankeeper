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
        # 2s 间隔（2026-10-04 上调：1s+实测密集请求撞过一次风控——探活回 302 跳风控页，
        # 只能等它自己解封；宁慢勿封）
        self.gate = gate or RateGate("115", min_interval=2.0, cooldown=60.0)
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
        if resp.status_code in (301, 302, 307, 308):
            # 302 = 115 把请求跳到风控/验证页（2026-10-04 实测：密集请求后探活即 302）。
            # 这是账号级临时封禁，只有等——明确报人话，别让上层显示"响应非 JSON"这种废话，
            # 同时让 RateGate 进退避，防止自动任务接着撞。
            self.gate.on_failure()
            raise AdapterError("115 触发风控（HTTP 302 跳转），请暂停 10-30 分钟再试，期间勿反复点检测/转存")
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
        if resp.status_code in (301, 302, 307, 308):
            self.gate.on_failure()
            raise AdapterError("115 触发风控（HTTP 302 跳转），请暂停 10-30 分钟再试，期间勿反复点检测/转存")
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
        # 单壳判定只看**根层条目**（2026-10-04 套娃案同款教训，baidu/quark 对齐）
        roots_raw = self._snap_page(share_code, receive_code, cid="")
        roots = [self._row_to_file(r, "") for r in roots_raw]
        # 「建壳转存」用（搜索转存快速弹窗）：单壳时记下根文件夹条目；非单壳/未开子目录置空
        self._root_shell = roots[0] if (spec.include_subdirs and len(roots) == 1 and roots[0].is_dir) else None
        strip_root = len(roots) == 1 and roots[0].is_dir  # 单壳剥壳：相对路径不含壳名
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
        # 接收上下文存给 save_files
        self._share_ctx = {"share_code": share_code, "receive_code": receive_code}
        return out

    def _snap_page(self, share_code: str, receive_code: str, cid: str) -> list[dict]:
        """share/snap 翻页到 count（别学 limit=20 不翻页的反面教材）。

        ⚠️ 实测响应两种形态（2026-10-04）：带 cid（子目录）时 list/count 在顶层；
        不带 cid（根层）时嵌在 data.count/data.list 里。两种都接，别按一种写死。"""
        rows: list[dict] = []
        offset = 0
        for _ in range(100):  # 10 万项封顶
            params = {"share_code": share_code, "receive_code": receive_code, "offset": offset, "limit": PAGE}
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
        """文件条目带哈希字段，目录没有。"""
        return bool(row.get("sha") or row.get("sha1"))

    @staticmethod
    def _row_to_file(row: dict, base: str) -> ShareFile:
        """snap 行 → ShareFile。

        ⚠️ 目录判定**不能依赖 fid**（实测有的分享目录行 fid=null，如 115cdn 单壳根），
        统一按"文件带 sha/sha1"判；id 取 fid 或 cid（TgtoDrive 同款 fallback）。"""
        name = Pan115Adapter._row_name(row)
        is_dir = not Pan115Adapter._is_file_row(row)
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
        """列自己的目录一层（webapi /files）。

        ⚠️ 实测响应（2026-10-04）：顶层数据是**扁平 list**——`data` 直接是条目数组、
        `count` 在顶层，不是 quark 那种 data.list 嵌套；照嵌套写会 AttributeError。
        ⚠️ 自己网盘的条目 id 在 **cid**（目录/文件都是），没有 fid——fid 是分享 snap
        侧的字段，两套清单别混（目录浏览/ensure_dir 全在 own-dir 侧）。"""
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
        """own-dir 行是否目录：文件条目带哈希字段，目录没有；条目 id 一律在 cid。"""
        return not (row.get("sha") or row.get("sha1"))

    @staticmethod
    def _row_id(row: dict) -> str:
        """own-dir 条目 id（目录/文件都在 cid）。"""
        return str(row.get("cid") or row.get("fid") or "")

    def path_to_cid(self, dir_path: str) -> str:
        """网盘路径 → cid（逐层下钻；根=0）。不存在抛 AdapterError。"""
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
        """webapi 建目录，返回新目录 cid；返回体缺 cid 时回落父目录查找（已存在场景）。"""
        # ⚠️ files/add 的目录名字段实测是 cname（dirname 会报"目录名称不能为空"，2026-10-04）
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
        """逐级建目录，返回末级 cid。"""
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

    # ---------- 目录管理（浏览弹窗的新建/重命名/删除，2026-10-04） ----------

    def create_dir(self, parent_cid: str, name: str) -> str:
        """在指定父目录下建文件夹，返回新 cid。与 ensure_dir 的逐层建同款接口。"""
        return self._mkdir(parent_cid, name)

    def rename_dir(self, cid: str, new_name: str) -> None:
        """重命名（目录/文件同一接口）。

        ⚠️ 端点/表单是 115driver（alist 同款）实测语义：POST files/batch_rename，
        **三个字段必须齐**：fid + file_name + files_new_name[<cid>]——只发 file_id/file_name
        或走 files/rename 都回"服务器开小差了"（2026-10-04 实测连环踩）。"""
        body = self._post(
            "https://webapi.115.com/files/batch_rename",
            {"fid": cid, "file_name": new_name, f"files_new_name[{cid}]": new_name},
        )
        self._check_state(body, "重命名")

    def delete_dir(self, cid: str) -> None:
        """删除目录（递归）——移入回收站（rb/delete），误删可从回收站捞回。"""
        body = self._post("https://webapi.115.com/rb/delete", {"pid": 0, "fid[0]": cid, "ignore_warn": 1})
        self._check_state(body, "删除")

    def save_files(self, files: list[ShareFile], spec: TaskSpec, on_progress, on_log) -> TransferResult:
        ctx = getattr(self, "_share_ctx", None)
        if not ctx:
            raise AdapterError("分享上下文缺失（save_files 必须跟在 list_share 之后）")
        result = TransferResult()
        # 「建壳转存」（spec.with_shell，搜索/普通转存弹窗）：按资源名/更名值新建文件夹、
        # 剥壳转入。早期 return——跳过文件级去重（建壳模式按"目标已有同名文件夹"去重）
        if spec.with_shell and not spec.only_paths:
            return self._save_with_shell(spec, getattr(self, "_root_shell", None), files, result, on_progress, on_log)
        save_list = [f for f in files if not f.is_dir and f.fid]

        # 勾选清单过滤（bdsavePro new_files 语义）：勾了文件=只转这些；勾了目录=整棵子树。
        # ⚠️ 必须有——实测漏了会把全量清单都收进去（2026-10-04：只想转 1 集收了 82 集）
        if spec.only_paths:
            def _kept(f: ShareFile) -> bool:
                for sel in spec.only_paths or set():
                    sel = sel.strip("/")
                    if sel and (f.path == sel or f.path.startswith(sel + "/")):
                        return True
                return False
            before = len(save_list)
            save_list = [f for f in save_list if _kept(f)]
            result.skip += before - len(save_list)

        # 去重（MD5 115 不提供，按名字）：基线 = compare_path ∪ save_dir 两边都扫。
        # 只看单边有真空窗：刚转存的文件躺在 save_dir 等 QMS 搬进库，窗口期重跑会重复转（40/41 案）
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
            self._receive_group(ctx, group, cid, spec, result, on_log)
            done += len(group)
            on_progress(min(99, int(done / total * 100)))
        on_progress(100)
        return result

    def _save_with_shell(self, spec: TaskSpec, shell: ShareFile | None, files: list[ShareFile], result: TransferResult, on_progress, on_log) -> TransferResult:
        """建壳承接（2026-10-04 用户定稿，baidu/quark 对齐）：在 save_dir 下按「更名值或
        资源名」新建文件夹当壳，分享内容剥壳转进去。去重口径：目标已有同名文件夹 → 整单跳过。
        115 特性：目录可整组接收——根层的**文件夹整目录接收**（一个 fid 带整棵子树，
        请求越少越远离风控），散文件才逐组收。"""
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
                # 根层 = 1 个文件夹 + N 散文件：剥原壳，内容进新壳（防同名套娃，新壳名替代原壳名）
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
            # 逐层确保 rel 目录存在——从 base_cid 出发，不重复列上层（省请求）
            cid = base_cid
            for seg in [p for p in rel.split("/") if p]:
                rows = self._list_own_dir(cid)
                match = next(
                    (r for r in rows if self._row_name(r) == seg and self._is_dir_row(r)),
                    None,
                )
                cid = self._row_id(match) if match else self._mkdir(cid, seg)
            return cid

        # 115 特性：**文件夹可整目录接收**（一个 fid 带整棵子树）——根层文件夹整个收，
        # 子树文件绝不再单独收（重复转存）。两个例外按文件接收：
        # ① 根层散文件；② "剥原壳"场景（原壳名被新壳替代，原壳 fid 不能收）；
        # ③ 配了 rename_map（自动任务正则改名）——整目录收无法改名，退回逐文件。
        # 壳条目本身（fid=S）绝不能整收——它的名字是新壳替代掉的，收进来就是套娃。
        use_whole_dir = not spec.rename_map
        top_dirs = [
            f for f in files if f.is_dir and f.fid and "/" not in f.path and f is not shell
        ] if use_whole_dir else []
        loose = [f for f in files if not f.is_dir and f.fid and "/" not in f.path]
        deeper = [f for f in files if not f.is_dir and f.fid and "/" in f.path]
        if shell is not None:
            deeper = deeper if not use_whole_dir else []  # 单壳：整目录接收已覆盖子树
        else:
            roots_dirs = [f for f in files if f.is_dir and f.fid and "/" not in f.path]
            if len(roots_dirs) == 1 and (loose or deeper):
                # 根层 = 1 个文件夹 + N 散文件：剥原壳——原壳名被新壳替代，原壳 fid 不能整收；
                # 原壳直接文件已被剥成顶层散文件，其子目录文件仍带路径，按目录 ensure 后接收
                top_dirs = []
                on_log(f"分享根层为「{roots_dirs[0].name}」+散文件：剥原壳，内容进新壳「{shell_name}」")
            else:
                deeper = deeper if not use_whole_dir else []  # 多文件夹混杂：整目录接收已覆盖子树
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
        """整组接收。整目录接收（files=[目录条目]）时用 add_count/extra_names 把子树内
        真实文件数与文件名记进账——run_watch/推送都按文件名对 QMS 记录，账要记文件。"""
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
            # 逐文件模式：记文件本身；整目录模式（add_count 给定）只记子树内真实文件名，
            # 目录条目自己不是文件，混进账里会污染 run_watch/推送的文件名匹配
            for f in files:
                result.transferred.append({"name": f.target_name or f.name, "fid": f.fid})
        for n in extra_names or []:
            result.transferred.append({"name": n, "fid": ""})
        if add_count is None:
            self._apply_renames(files, cid, spec, result, on_log)

    def _apply_renames(self, group: list[ShareFile], cid: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        """转存后改名（正则目标名 ≠ 原名时）。receive 不带改名：列一次目标目录按
        原名匹配到文件 id，逐条 files/rename（115 无 MD5 去重，名字即唯一依据）。"""
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
        """会员 + 容量摘要。

        会员走 get_user_aq 的 vip 字段（is_vip/expire_str，实测 2026-10-04）；
        无会员/拿不到都按「普通用户」显示，别留空白。
        容量走 get_storage_info（web Cookie 可用）：返回按空间分区（永久/转存等）
        的 {total, used} 字典——各分区求和即为账号总容量。"""
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
            pass  # 摘要拿不到会员就按普通用户，别影响账号卡片
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
            pass  # 容量拿不到就留空（前端显示"暂无容量信息"）
        return {"capacity": cap, "vip": vip}
