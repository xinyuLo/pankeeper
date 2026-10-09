from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time

import httpx
import requests

from ..security import decrypt_credential
from ..services import reqstat
from .base import AdapterError, CloudAdapter, CredentialExpired, ShareBanned, ShareFile, TaskSpec, TransferResult
from .rate_gate import RateGate

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

SHARE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/77.0.3865.75 Safari/537.36"
)

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
        data = self._get("https://pan.baidu.com/api/quota", {"checkfree": 1, "checkexpire": 1})
        if data.get("errno") != 0:
            return None
        total, used = data.get("total"), data.get("used")
        if not total:
            return None
        return {"total": int(total), "used": int(used or 0)}

    def vip_info(self) -> dict | None:
        try:
            info = self._get("https://pan.baidu.com/rest/2.0/xpan/nas", {"method": "uinfo"})
        except (AdapterError, CredentialExpired):
            return None
        if info.get("errno") != 0:
            return None
        data = info.get("data") or info

        vip_type = int(data.get("vip_type") or 0)
        is_vip = int(data.get("is_vip") or 0)
        if not is_vip and not vip_type:
            return {"name": "普通用户", "expires": None}
        name = {1: "会员", 2: "超级会员", 4: "超级会员"}.get(vip_type, "会员")
        return {"name": name, "expires": None}

    def list_dir(self, directory: str = "/") -> list[dict]:
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
            if errno == -6:
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

    _SHARELINK_PREFIX = re.compile(r"^/sharelink\d*-\d+")

    @staticmethod
    def parse_share_url(url: str) -> str:
        url = (url or "").strip().split("#")[0]
        m = re.search(r"/share/init\?surl=([a-zA-Z0-9_-]+)", url)
        if m is None:
            m = re.search(r"/s/([a-zA-Z0-9_-]+)", url)
        if m is None:
            raise AdapterError(f"无法识别的百度分享链接：{url[:80]}")
        slug = m.group(1)
        if len(slug) < 20:
            raise AdapterError(
                f"分享码不完整（{len(slug)} 位，正常 22/23 位）——链接疑似被来源截断，请换一条或手动补全：{url[:80]}"
            )
        return slug

    @staticmethod
    def _verify_surl(slug: str) -> str:
        return slug[1:] if len(slug) >= 22 and slug.startswith("1") else slug

    def _share_session(self) -> requests.Session:
        if getattr(self, "_share_cli", None) is None:
            jar: dict[str, str] = {}
            for part in self.cookies.split(";"):
                if "=" in part:
                    k, _, v = part.strip().partition("=")
                    jar[k] = v
            self._base_cookie_jar = jar
            cli = requests.Session()
            cli.cookies.update(jar)
            cli.headers.update({"user-agent": SHARE_UA})
            self._share_cli = cli
        return self._share_cli

    _XHR_HEADERS = {
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://pan.baidu.com",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }

    def _share_post(self, url: str, *, params: dict, data: dict, referer: str) -> dict:
        self.gate.wait()
        headers = {**self._XHR_HEADERS, "referer": referer} if referer else {**self._XHR_HEADERS}
        try:
            resp = self._share_session().post(url, params=params, data=data, headers=headers, timeout=20.0)
        except requests.RequestException as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        try:
            body = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        reqstat.bump("baidu")
        self.gate.on_success()
        return body

    def _share_get(self, url: str, *, params: dict | None = None, referer: str) -> dict:
        self.gate.wait()
        headers = {"referer": referer} if referer else {}
        try:
            resp = self._share_session().get(url, params=params, headers=headers, timeout=20.0)
        except requests.RequestException as e:
            self.gate.on_failure()
            raise AdapterError(f"网络异常：{e}") from e
        try:
            body = resp.json()
        except ValueError as e:
            self.gate.on_failure()
            raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
        reqstat.bump("baidu")
        self.gate.on_success()
        return body

    @staticmethod
    def _check_share_errno(data: dict, action: str) -> None:
        errno = data.get("errno")
        if errno in (0, None):
            return
        if errno in (-7, -8, -9, 115, 145):
            raise ShareBanned(f"{action}：errno={errno}（分享已删除/过期/禁止分享）")
        if errno == -6 or errno in (31041, 31042):
            raise CredentialExpired("百度 Cookie 已失效")
        if errno == -12:
            raise AdapterError("提取码错误")
        if errno in (-62, -19):
            raise AdapterError("触发验证码，请稍后再试或手动过一次验证")
        raise AdapterError(f"{action}失败：errno={errno}")

    def _verify_password(self, surl: str, pwd: str) -> None:
        cli = self._share_session()
        last_body: dict = {}
        last_resp: requests.Response | None = None

        for attempt in range(2):
            self.gate.wait()
            try:
                resp = cli.post(
                    "https://pan.baidu.com/share/verify",
                    params={"surl": surl, "t": int(time.time() * 1000), "channel": "chunlei", "web": 1, "bdstoken": "null", "clienttype": 0},
                    data={"pwd": pwd},

                    headers={"referer": f"https://pan.baidu.com/share/init?surl={surl}"},
                    timeout=20.0,
                )
            except requests.RequestException as e:
                self.gate.on_failure()
                raise AdapterError(f"网络异常：{e}") from e
            try:
                body = resp.json()
            except ValueError as e:
                self.gate.on_failure()
                raise AdapterError(f"响应非 JSON（HTTP {resp.status_code}）") from e
            self.gate.on_success()
            reqstat.bump("baidu")
            last_body = body
            last_resp = resp
            if body.get("errno") in (0, None):
                break
            if body.get("errno") in (-7, -9, -62) and attempt == 0:
                print(f"[baidu] verify errno={body.get('errno')}（疑似风控抖动），3 秒后重试一次", flush=True)
                time.sleep(3)
                continue
        self._check_share_errno(last_body, "提取码验证")
        bdclnd = last_body.get("randsk") or ""
        if bdclnd:

            jar = dict(self._base_cookie_jar)
            if last_resp is not None:
                for k, v in last_resp.cookies.get_dict().items():
                    if k != "BDCLND":
                        jar[k] = v
            jar["BDCLND"] = bdclnd
            cli.cookies.clear()
            cli.cookies.update(jar)

    def _fetch_share_page(self, page_slug: str) -> dict:
        cli = self._share_session()

        def _grab():
            self.gate.wait()
            try:
                resp = cli.get(f"https://pan.baidu.com/s/{page_slug}", headers={"referer": "https://pan.baidu.com/"}, timeout=20.0)
            except requests.RequestException as e:
                self.gate.on_failure()
                raise AdapterError(f"网络异常：{e}") from e
            self.gate.on_success()
            reqstat.bump("baidu")
            return resp

        def _parse(h: str):
            return re.search(r"yunData\.setData\((\{.*?\})\)\s*;", h, re.S) or re.search(r"locals\.mset\((\{.*?\})\)\s*;", h, re.S)

        resp = _grab()
        html = resp.text
        m = _parse(html)

        for round_ in range(2):
            if m is not None or resp.status_code == 404 or "页面不存在" in html:
                break
            print(f"[baidu] 分享页未含 yunData（抢救轮 {round_ + 1}）：init 预热" + ("+重新验证提取码" if round_ else "") + "后重抓", flush=True)
            self.gate.wait()
            try:
                cli.get(f"https://pan.baidu.com/share/init?surl={page_slug[1:]}", headers={"referer": "https://pan.baidu.com/"}, timeout=20.0)
            except requests.RequestException:
                pass
            self.gate.on_success()
            reqstat.bump("baidu")
            if round_ and getattr(self, "_last_pwd", ""):
                try:
                    self._verify_password(self._verify_surl(page_slug), self._last_pwd)
                except AdapterError:
                    pass
            resp = _grab()
            html = resp.text
            m = _parse(html)
            if m is None and round_ == 0:
                time.sleep(3)
        if m is None:
            if "页面不存在" in html or resp.status_code == 404:
                raise ShareBanned("分享链接不存在（页面 404）")
            if re.search(r"输入提取码|提取码：|share-verif", html):
                raise AdapterError("该分享需要提取码，但请求里没有带上（请补提取码后重试）")
            if re.search(r"分享的文件已经被取消|分享已过期|分享不存在|链接不存在", html):
                raise ShareBanned("分享页提示链接已失效")

            title = re.search(r"<title>([^<]{0,60})", html)
            feat = title.group(1).strip() if title else f"{len(html)} 字节、无 title"
            if re.search(r"安全验证|滑动验证|验证码|wakeup|sec\.php|风险", html):
                raise AdapterError(
                    f"百度触发安全验证（疑似风控），页面：{feat}——过几分钟重试，或先在浏览器里打开一次该分享再点转存"
                )
            raise AdapterError(f"分享页解析失败（未找到 yunData）· 页面特征：{feat}")
        try:
            return json.loads(m.group(1))
        except ValueError as e:
            raise AdapterError(f"yunData JSON 解析失败：{e}") from e

    def _share_list_dir(self, ctx: dict, dir_path: str) -> list[dict]:
        out: list[dict] = []
        page = 1
        for _ in range(50):
            self._pace_share_list()
            data = self._share_get(
                "https://pan.baidu.com/share/list",
                params={
                    "channel": "chunlei",
                    "clienttype": 0,
                    "web": 1,
                    "page": page,
                    "num": 100,
                    "dir": dir_path,
                    "t": str(random.random()),
                    "uk": ctx["uk"],
                    "shareid": ctx["shareid"],
                    "desc": 1,
                    "order": "other",
                    "bdstoken": "null",
                    "showempty": 0,
                },
                referer="",
            )
            self._check_share_errno(data, "分享目录清单")
            self._mark_share_list()
            rows = data.get("list") or []
            out.extend(rows)
            if len(rows) < 100:
                return out
            page += 1
        return out

    _SHARE_LIST_GAP = 2.0

    def _pace_share_list(self) -> None:
        last = getattr(self, "_last_sl_ts", 0.0)
        wait = self._SHARE_LIST_GAP - (time.time() - last)
        if wait > 0:
            time.sleep(wait)

    def _mark_share_list(self) -> None:
        self._last_sl_ts = time.time()

    @classmethod
    def _share_row_to_file(cls, row: dict, base: str) -> ShareFile:
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
        slug = self.parse_share_url(spec.share_url)
        page_url = f"https://pan.baidu.com/s/{slug}"
        referer = page_url

        pwd = (spec.share_code or "").strip()
        if not pwd:
            m = re.search(r"[?&](?:pwd|password)=([a-zA-Z0-9]+)", spec.share_url or "")
            pwd = m.group(1) if m else ""
        if pwd:
            self._last_pwd = pwd
            self._verify_password(self._verify_surl(slug), pwd)
        page = self._fetch_share_page(slug)
        ctx = {
            "surl": slug,
            "referer": referer,
            "shareid": page.get("shareid"),

            "uk": page.get("share_uk") or page.get("uk") or page.get("share_uk"),
            "bdstoken": page.get("bdstoken") or "",
        }
        if not ctx["shareid"] or not ctx["uk"]:
            raise AdapterError("分享页缺少 shareid/uk（分享可能已失效）")
        self._share_ctx = ctx

        files: list[ShareFile] = []
        roots_raw = page.get("file_list") or []
        roots = [self._share_row_to_file(r, "") for r in roots_raw]
        files.extend(roots)

        self._root_shell = roots[0] if (spec.include_subdirs and len(roots) == 1 and roots[0].is_dir) else None
        if spec.include_subdirs:

            strip_root = len(roots) == 1 and roots[0].is_dir
            for raw, node in zip(roots_raw, roots):
                if not node.is_dir:
                    continue
                abs_dir = raw.get("path") or f"/{node.name}"
                base = "" if strip_root else node.name
                for row in self._share_list_dir(ctx, abs_dir):
                    f = self._share_row_to_file(row, base)
                    files.append(f)
                    if f.is_dir:
                        files.extend(self._walk_share_dir(ctx, row.get("path") or f"{abs_dir}/{f.name}", f.path))

        if spec.exclude_names or spec.exclude_md5s:
            files = [
                f
                for f in files
                if f.name not in spec.exclude_names and not (not f.is_dir and f.md5 and f.md5 in spec.exclude_md5s)
            ]
        return files

    def _walk_share_dir(self, ctx: dict, abs_dir: str, base: str) -> list[ShareFile]:
        out: list[ShareFile] = []
        for row in self._share_list_dir(ctx, abs_dir):
            f = self._share_row_to_file(row, base)
            out.append(f)
            if f.is_dir:

                out.extend(self._walk_share_dir(ctx, row.get("path") or f"{abs_dir}/{f.name}", f.path))
        return out

    def list_dir_names(self, dir_path: str) -> set[str]:
        names, _ = self._dir_baselines(dir_path)
        return names

    def _dir_baselines(self, dir_path: str) -> tuple[set[str], set[str]]:
        names: set[str] = set()
        md5s: set[str] = set()
        for e in self.list_dir(dir_path):
            if int(e.get("isdir") or 0):
                continue
            names.add(self._basename(e))
            if e.get("md5"):
                md5s.add(str(e["md5"]).lower())
        return names, md5s

    @staticmethod
    def _basename(entry: dict) -> str:
        return entry.get("server_filename") or (entry.get("path") or "/").rsplit("/", 1)[-1]

    def _mkdir(self, path: str) -> None:
        data = self._get(
            "https://pcs.baidu.com/rest/2.0/pcs/file",
            {"method": "mkdir", "path": path, "app_id": 778750},
        )
        errno = data.get("errno")
        if errno in (0, 12, None):
            return
        if errno == -8:
            raise AdapterError(f"目录名非法：{path}")
        if errno in (-6, 31041, 31042):
            raise CredentialExpired("百度 Cookie 已失效")
        if errno == -32:
            raise AdapterError("网盘空间不足")
        raise AdapterError(f"建目录失败 {path}：errno={errno}")

    def create_dir(self, parent_path: str, name: str) -> str:
        full = parent_path.rstrip("/") + "/" + name
        self._mkdir(full)
        return full

    def rename_dir(self, path: str, new_name: str) -> str:
        parent = path.rstrip("/").rsplit("/", 1)[0]
        full = (parent + "/" + new_name) if parent else "/" + new_name
        body = self._share_post(
            "https://pan.baidu.com/rest/2.0/xpan/file",
            params={"method": "filemanager", "web": 1},
            data={"filelist": json.dumps([{"path": path, "newname": new_name}], ensure_ascii=False)},
            referer="https://pan.baidu.com/disk/main",
        )
        if int(body.get("errno") or 0) != 0:
            raise AdapterError(f"重命名失败：errno={body.get('errno')}")
        return full

    def delete_dir(self, path: str) -> None:
        body = self._share_post(
            "https://pan.baidu.com/rest/2.0/xpan/file",
            params={"method": "filemanager", "web": 1},
            data={"oper": "delete", "filelist": json.dumps([{"path": path}], ensure_ascii=False)},
            referer="https://pan.baidu.com/disk/main",
        )
        if int(body.get("errno") or 0) != 0:
            raise AdapterError(f"删除失败：errno={body.get('errno')}")

    def _ensure_dirs(self, dirs: list[str]) -> None:

        for d in dirs:
            try:
                self.list_dir(d)
                continue
            except CredentialExpired:
                raise
            except AdapterError:
                pass
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

        if spec.with_shell:
            return self._save_with_shell(spec, getattr(self, "_root_shell", None), files, result, on_progress, on_log)
        save_list = [f for f in files if not f.is_dir and f.fid]

        if spec.only_paths:
            def _kept(f: ShareFile) -> bool:
                rel = f.path.strip("/")
                for sel in spec.only_paths or set():
                    sel = sel.strip("/")
                    if not sel:
                        continue
                    if rel == sel or rel.startswith(sel + "/"):
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
        md5s: set[str] = set()
        for base in bases:
            try:

                dir_names, dir_md5s = self._dir_baselines(base)
                existing |= dir_names
                md5s |= dir_md5s
            except (AdapterError, CredentialExpired) as e:
                on_log(f"对比目录 {base} 读取失败（{e}），该目录不参与去重基线")
        need = []
        for f in save_list:
            if f.md5 and f.md5 in md5s:
                result.skip += 1
                result.skip_md5 += 1
                result.md5_skipped.append({"name": f.name, "md5": f.md5})
            elif (f.target_name or f.name) in existing or f.name in existing:
                result.skip += 1
            else:
                need.append(f)
        on_log(f"清单 {len(save_list)} 项：去重跳过 {result.skip} / 待转存 {len(need)}")
        if not need:
            on_progress(100)
            return result

        rel_dirs = sorted({f.path.rsplit("/", 1)[0] for f in need if "/" in f.path})
        if rel_dirs:
            self._ensure_dirs([spec.save_dir.rstrip("/") + "/" + d for d in rel_dirs])

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

    def _save_with_shell(self, spec: TaskSpec, shell: ShareFile | None, files: list[ShareFile], result: TransferResult, on_progress, on_log) -> TransferResult:
        shell_name = (
            (spec.folder_rename or "").strip()
            or (spec.share_name or "").strip()
            or (shell.name if shell is not None else "")
            or "分享资源"
        )
        target = spec.save_dir.rstrip("/") + "/" + shell_name
        try:
            self.list_dir(target)
            result.skip += 1
            on_log(f"目标已存在同名文件夹「{shell_name}」，跳过转存")
            on_progress(100)
            return result
        except CredentialExpired:
            raise
        except AdapterError:
            pass

        if shell is None:
            roots_dirs = [f for f in files if f.is_dir and f.fid and "/" not in f.path]
            if len(roots_dirs) == 1:
                on_log(f"分享根层为「{roots_dirs[0].name}」+散文件：剥原壳，内容进新壳「{shell_name}」")
            else:
                on_log(f"分享无根文件夹（或多文件夹混杂），已建壳「{shell_name}」承接全部内容")
        on_log(f"在 {spec.save_dir.rstrip('/')} 下新建文件夹「{shell_name}」，剥壳转存分享内容")

        self._ensure_dirs([target])
        inner = [f for f in files if f.fid and not f.is_dir]

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
        for i in range(0, len(inner), 500):
            self._transfer_group(self._share_ctx, inner[i : i + 500], target, spec, result, on_log)
        on_progress(100)
        return result

    def _transfer_group(self, ctx: dict, group: list[ShareFile], target_dir: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        fsids = [int(f.fid) for f in group if str(f.fid).isdigit()]
        if not fsids:
            return

        params = {"shareid": ctx["shareid"], "from": ctx["uk"], "bdstoken": ctx["bdstoken"], "channel": "chunlei", "clienttype": 0, "web": 1}
        data = {"path": target_dir, "fsidlist": json.dumps(fsids)}
        body = self._share_post("https://pan.baidu.com/share/transfer", params=params, data=data, referer=ctx["referer"])
        if body.get("errno") == -65:

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
        for f in group:
            target = f.target_name
            if not target or target == f.name:
                continue

            time.sleep(0.5)
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
