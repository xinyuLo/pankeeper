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
# 分享链路专用 UA：对齐 baidupcs_py（pcs.py:39）。它用这个老 UA 千锤百炼；
# 主 client（quota/uinfo/xpan list）维持新 UA 没问题，分享接口的风控更神经质
SHARE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/77.0.3865.75 Safari/537.36"
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
        # 1s 间隔（2026-10-03 用户定稿：先试过 2s，回落到 research 区间下限）
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
        """任意形态的分享链接 → 页面短码（/s/xxx 的完整 xxx，含前导 1）。

        注意两套短码：页面地址 /s/<slug> 用**全长**；share/verify 的 surl 参数
        要去掉前导 1 的 22 位码——混用会 404/验证失效。
        ⚠️ 短码长度校验（2026-10-02）：正常 22/23 位。PanSou 部分数据源会截断
        链接（实测出现过 12 位），残码打百度只会得到畸形响应——直接报人话。
        """
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
        """verify 接口的 surl：去掉前导 1 的 22 位码。"""
        return slug[1:] if len(slug) >= 22 and slug.startswith("1") else slug

    def _share_session(self) -> requests.Session:
        """分享链路专用会话——**requests 而非 httpx**。

        ⚠️ 客户端选择是风控生死线（2026-10-02 实测定案）：同账号同链接同参数，
        baidupcs_py（requests）的 share/list 通过，我们（httpx）一律 errno=-7——
        百度按客户端特征（TLS 指纹/头行为）区别对待。主 client（quota/uinfo/xpan）
        保持 httpx 没问题，**分享链路必须 requests**（baidupcs_py 同款）。
        主 client 的 Cookie 是静态 header（verify/quota/list 足够），但分享验证会
        Set-Cookie: BDCLND，必须用可变 cookie jar——按 cookie 串单独建一个，
        解析失败/网盘抖动只影响本次转存，不污染主客户端。
        """
        if getattr(self, "_share_cli", None) is None:
            jar: dict[str, str] = {}
            for part in self.cookies.split(";"):
                if "=" in part:
                    k, _, v = part.strip().partition("=")
                    jar[k] = v
            self._base_cookie_jar = jar  # verify 重建 jar 时要用
            cli = requests.Session()
            cli.cookies.update(jar)
            cli.headers.update({"user-agent": SHARE_UA})
            self._share_cli = cli
        return self._share_cli

    # 网页内部接口（api/create、api/filemanager、share/transfer 等）会校验请求
    # 是否「从网盘页面发出的 XHR」：缺 X-Requested-With / Origin 一律回 errno=-6，
    # 与 Cookie 是否有效无关。baidupcs_py 正是靠这三个头才跑得通。
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
        """分享链路 GET（share/list 等）。

        ⚠️ 头部纪律：只带 UA + Referer——share/list 带 X-Requested-With/Origin
        会被回 errno=-7（与 verify 同一陷阱）。XHR 三件套只有 share/transfer 需要。
        """
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
        """分享链路错误码分流：死链熔断 / 凭据失效 / 一般失败。
        action 进异常文案——日志里要能分清 -7 是 verify 还是 share/list 回的。"""
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
        """提取码验证；成功后把 randsk 显式写进会话 cookie（BDCLND）。

        不能依赖 httpx 自动收 Set-Cookie：百度对 BDCLND 会下发两份（domain 属性
        差异），请求头里出现重复 Cookie 时百度仍按未验证处理，页面拿不到 yunData。
        所以这里拿到 randsk 后清空 jar 重建：基础 cookie + 单份 BDCLND。

        ⚠️ 头部纪律（2026-10-01 实测踩坑）：verify 是**同源 POST**——浏览器同源
        请求本就不带 Origin。baidupcs_py 的 verify 也只发 UA + Referer（init 页形态）。
        给 verify 加 XHR 三件套会被判异常回 errno=-7（链接明明是活的）。
        transfer/api-create 则相反，必须带 XHR 头——两类接口纪律相反，别统一。
        """
        cli = self._share_session()
        last_body: dict = {}
        last_resp: requests.Response | None = None
        # -7/-9/-62 常是风控抖动而非死链（baidupcs_py 把 -9 当验证码场景重试；
        # bdsavePro 对全部 API 套 retry(1, 2~3s)）：隔 3s 重试一次再下结论
        for attempt in range(2):
            self.gate.wait()
            try:
                resp = cli.post(
                    "https://pan.baidu.com/share/verify",
                    params={"surl": surl, "t": int(time.time() * 1000), "channel": "chunlei", "web": 1, "bdstoken": "null", "clienttype": 0},
                    data={"pwd": pwd},
                    # 只带 UA + Referer（share/init 页形态），与 baidupcs_py 完全一致
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
            # baidupcs_py 语义（pcs.py:742 _cookies_update）：verify 响应下发的 cookie
            # 要**合并**进会话——里面可能有风控相关的非 BDCLND cookie。
            # BDCLND 仍显式单份（防百度 domain 差异造成的双份 header）。
            jar = dict(self._base_cookie_jar)
            if last_resp is not None:
                for k, v in last_resp.cookies.get_dict().items():
                    if k != "BDCLND":
                        jar[k] = v
            jar["BDCLND"] = bdclnd
            cli.cookies.clear()
            cli.cookies.update(jar)

    def _fetch_share_page(self, page_slug: str) -> dict:
        """分享页 HTML 抓 yunData.setData(...) JSON：shareid/uk/bdstoken/file_list。

        page_slug 是**全长**短码（/s/1xxx 的 1xxx）——用 verify 那个 22 位短码会 404。

        ⚠️ 冷启动 fallback（2026-10-02 实测）：部分分享首次抓页拿不到 yunData
        （会话冷启动，尤其新分享），用户手动「点一下链接」后重试就通——浏览器的
        等价行为是先经 share/init 提码页再回列表页。这里自动补一次 init 预热后
        重抓，等效于那次手动点击，无需人工干预。
        """
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
        if m is None and resp.status_code != 404 and "页面不存在" not in html:
            # 冷启动 fallback：预热 init 提码页（建立会话上下文）后重抓一次
            print(f"[baidu] 分享页未含 yunData（疑似会话冷启动），init 预热后重抓", flush=True)
            self.gate.wait()
            try:
                cli.get(f"https://pan.baidu.com/share/init?surl={page_slug[1:]}", headers={"referer": "https://pan.baidu.com/"}, timeout=20.0)
            except requests.RequestException:
                pass  # 预热失败不致命，沿用原 html 走错误分流
            self.gate.on_success()
            reqstat.bump("baidu")
            resp = _grab()
            html = resp.text
            m = _parse(html)
        if m is None:
            if "页面不存在" in html or resp.status_code == 404:
                raise ShareBanned("分享链接不存在（页面 404）")
            if re.search(r"输入提取码|提取码：|share-verif", html):
                raise AdapterError("该分享需要提取码，但请求里没有带上（请补提取码后重试）")
            if re.search(r"分享的文件已经被取消|分享已过期|分享不存在|链接不存在", html):
                raise ShareBanned("分享页提示链接已失效")
            raise AdapterError("分享页解析失败（未找到 yunData，可能需要先过提取码验证）")
        try:
            return json.loads(m.group(1))
        except ValueError as e:
            raise AdapterError(f"yunData JSON 解析失败：{e}") from e

    def _share_list_dir(self, ctx: dict, dir_path: str) -> list[dict]:
        """分享内子目录清单（/share/list，100/页翻页）。

        ⚠️ 参数必须齐装（2026-10-02 实测）：channel/clienttype/web/bdstoken/showempty
        缺任何一个，百度回 errno=-7（"分享已删除"是它对不合法请求的万能筐）。
        参数形态 1:1 对齐 baidupcs_py pcs.py:798-812，Referer 也不发（同款）。
        ⚠️ 防风控：share/list 连续调用之间强制 2 秒间隔（转存 walk 与下钻浏览都走这里）。
        """
        out: list[dict] = []
        page = 1
        for _ in range(50):  # 5000 项封顶，防异常死循环
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

    _SHARE_LIST_GAP = 2.0  # share/list 相邻两次调用的最小间隔（秒）

    def _pace_share_list(self) -> None:
        """保证 share/list 相邻调用间隔 ≥ 2s（实例级时间戳，覆盖转存 walk / 下钻浏览）。"""
        last = getattr(self, "_last_sl_ts", 0.0)
        wait = self._SHARE_LIST_GAP - (time.time() - last)
        if wait > 0:
            time.sleep(wait)

    def _mark_share_list(self) -> None:
        self._last_sl_ts = time.time()

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
        slug = self.parse_share_url(spec.share_url)
        page_url = f"https://pan.baidu.com/s/{slug}"
        referer = page_url
        # 提取码三个来源都认：独立字段 > 链接 ?pwd= > 链接 password=。
        # 只要把 pwd 放在链接里（快速转存的常态）也必须先 verify 拿 BDCLND，
        # 否则抓到的是「输入提取码」页面，yunData 根本不存在——bdsavepro 同款顺序。
        pwd = (spec.share_code or "").strip()
        if not pwd:
            m = re.search(r"[?&](?:pwd|password)=([a-zA-Z0-9]+)", spec.share_url or "")
            pwd = m.group(1) if m else ""
        if pwd:
            self._verify_password(self._verify_surl(slug), pwd)
        page = self._fetch_share_page(slug)
        ctx = {
            "surl": slug,
            "referer": referer,
            "shareid": page.get("shareid"),
            # ⚠️ 必须是**分享者**的 uk：yunData 里 share_uk=分享者，uk=当前登录用户——
            # 顺序取反的话 share/list 回 -7（"啊哦，链接出错了"）、transfer 的 from 也错。
            # 2026-10-02 与 baidupcs_py 抓包对比实锤（金标准 uk=dir 前缀里的那个 13 位 id）。
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
        # 「带壳转存」用（搜索转存快速弹窗）：单壳时记下根文件夹条目——fsid 可直接进
        # fsidlist 整体转存（目标自带文件夹名）；非单壳 / 未开子目录时置空（无壳可带或结构本就保留）
        self._root_shell = roots[0] if (spec.include_subdirs and len(roots) == 1 and roots[0].is_dir) else None
        if spec.include_subdirs:
            # ⚠️ share/list 的 dir 必须用**原始路径**（含 /sharelink<id>-<uk> 前缀，
            # 即 yunData file_list 返回的 path）——传剥了前缀的逻辑路径一律 -7。
            # 2026-10-02 逐字节对比 baidupcs_py 抓包实锤（此前 M3 起从未跑对过）。
            # 单文件夹壳自动剥（quark 同款语义，即 NAS 工具 keep_folder=false）：
            # 根层只有 1 个文件夹、没有散文件时，以它为根遍历、相对路径不含壳名，
            # 否则 save_dir 下会白套一层「兰丨香如故/」。
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

        # 排除清单：文件名或 MD5 任一命中即排除（目录只按名字——整目录排除）
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
                # 递归同样用原始路径（含 /sharelink 前缀，见 list_share 注释）
                out.extend(self._walk_share_dir(ctx, row.get("path") or f"{abs_dir}/{f.name}", f.path))
        return out

    def list_dir_names(self, dir_path: str) -> set[str]:
        """base 契约方法：只要名字。内部走 _dir_baselines 同一份请求。"""
        names, _ = self._dir_baselines(dir_path)
        return names

    def _dir_baselines(self, dir_path: str) -> tuple[set[str], set[str]]:
        """一次 list_dir 同时收文件名与 MD5（去重基线）。

        bdsavePro 的 list_local_files 同款语义：名字与 MD5 本就来自同一份列表，
        分两次调只会把对百度目录接口的请求量翻倍——风控时代请求能省则省。
        """
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
        """建目录——PCS 通道（baidupcs_py pcs.py:430 同款）。

        ⚠️ 不要用网页版 POST api/create：对程序化请求回 -6/31041（2026-10-01/02
        两天的 -6/-7 连环案最后一环）。PCS 的 GET mkdir 走主 client 即可，
        errno 12 = 已存在算成功；-8 文件名非法。
        """
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

    # ---------- 目录管理（浏览弹窗的新建/重命名/删除，2026-10-03） ----------

    def create_dir(self, parent_path: str, name: str) -> str:
        """建目录，返回新目录完整路径（百度的 fid 就是路径）。已存在算成功（errno 12）。"""
        full = parent_path.rstrip("/") + "/" + name
        self._mkdir(full)
        return full

    def rename_dir(self, path: str, new_name: str) -> str:
        """重命名目录（filemanager oper=rename，与转存后改名的 _apply_renames 同款接口）。

        返回新完整路径。目录同样走这条通道，百度不区分文件/目录。"""
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
        """删除目录（filemanager oper=delete，百度目录删除是递归的，含全部内容）。"""
        body = self._share_post(
            "https://pan.baidu.com/rest/2.0/xpan/file",
            params={"method": "filemanager", "web": 1},
            data={"oper": "delete", "filelist": json.dumps([{"path": path}], ensure_ascii=False)},
            referer="https://pan.baidu.com/disk/main",
        )
        if int(body.get("errno") or 0) != 0:
            raise AdapterError(f"删除失败：errno={body.get('errno')}")

    def _ensure_dirs(self, dirs: list[str]) -> None:
        # bdsavePro _ensure_dir_tree_exists 语义：整树能 list 通 = 目录已存在，
        # 一个 mkdir 都不用发（重复转存同目录时省掉整串 api/create）。
        # 只有 list 失败（目录缺失）才逐级建；凭据失效必须原样上抛，别当"目录不存在"吞了。
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
        # 「带壳转存」（spec.with_shell，搜索转存快速弹窗）：单壳分享整壳转 + 可选根文件夹更名。
        # 早期 return——跳过文件级 MD5/名字去重（整壳模式按"目标已有同名文件夹"去重）
        if spec.with_shell and not spec.only_paths:
            shell = getattr(self, "_root_shell", None)
            if shell is None or shell.fid:
                return self._save_with_shell(spec, shell, files, result, on_progress, on_log)
        save_list = [f for f in files if not f.is_dir and f.fid]

        # 勾选清单过滤（bdsavePro new_files 语义）：勾了文件=只转这些；
        # 勾了目录=该目录整棵子树（按分享内相对路径前缀匹配）
        if spec.only_paths:
            def _kept(f: ShareFile) -> bool:
                for sel in spec.only_paths or set():
                    sel = sel.strip("/")
                    if not sel:
                        continue
                    if f.path == sel or f.path.startswith(sel + "/"):
                        return True
                return False
            before = len(save_list)
            save_list = [f for f in save_list if _kept(f)]
            result.skip += before - len(save_list)

        # 去重（MD5 优先 → 文件名）：基线 = compare_path ∪ save_dir 两边都扫。
        # 只看 compare_path 有真空窗：刚转存的文件躺在 save_dir 等 QMS 搬进库，
        # 窗口期基线里既没名字也没 MD5，cron 重跑会把同一集再存一遍（2026-10-03 40/41 重复案）。
        bases: list[str] = []
        if spec.compare_path:
            bases.append(spec.compare_path)
        if spec.save_dir and spec.save_dir not in bases:
            bases.append(spec.save_dir)
        existing: set[str] = set()
        md5s: set[str] = set()
        for base in bases:
            try:
                # 名字与 MD5 同源：一次扫描拿全（原两遍 list_dir 是给风控送人头的写法）
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

    def _save_with_shell(self, spec: TaskSpec, shell: ShareFile | None, files: list[ShareFile], result: TransferResult, on_progress, on_log) -> TransferResult:
        """带壳转存（搜索转存快速弹窗，spec.with_shell）：

        - 单壳（list_share 剥壳时记下的 `_root_shell`）：整壳转过来，`folder_rename` 非空时
          转存完成后 rename_dir 更名；
        - 非单壳（散文件/多文件夹，2026-10-04 用户："没壳的文件建个壳套进去，多文件也不用管了"）：
          在 save_dir 下建一个壳文件夹（壳名 = 更名值或分享名），根层条目全部转进壳——
          文件夹整转（自带名）、散文件直转，子树随文件夹整体过来不重复转。
        去重口径（两种模式一致）：目标已有同名文件夹 → 整壳跳过（没法逐文件 MD5 比对）。
        """
        shell_name = (spec.folder_rename or "").strip()
        if shell is not None:
            target = spec.save_dir.rstrip("/") + "/" + shell.name
            shown = shell.name
        else:
            shell_name = shell_name or (spec.share_name or "").strip() or "分享资源"
            target = spec.save_dir.rstrip("/") + "/" + shell_name
            shown = shell_name
        try:
            self.list_dir(target)
            result.skip += 1
            on_log(f"目标已存在同名文件夹「{shown}」，跳过整壳转存")
            on_progress(100)
            return result
        except CredentialExpired:
            raise
        except AdapterError:
            pass  # 目录不存在 = 没转过，继续

        if shell is not None:
            on_log(f"整壳转存分享根文件夹「{shell.name}」→ {spec.save_dir}/")
            # ⚠️ 真实 _transfer_group 第一个参数是分享 ctx（save_files 开头已校验存在），别漏
            self._transfer_group(self._share_ctx, [shell], spec.save_dir, spec, result, on_log)
            new_name = (spec.folder_rename or "").strip()
            if new_name and new_name != shell.name:
                time.sleep(1)  # 转存刚落库就 rename 是写操作连打，歇一拍防 -65
                self.rename_dir(target, new_name)
                result.renamed += 1
                if result.transferred:
                    result.transferred[0]["name"] = new_name
                on_log(f"根文件夹已更名：{shell.name} → {new_name}")
        else:
            self._ensure_dirs([target])
            on_log(f"分享无根文件夹，已建壳「{shell_name}」承接全部内容")
            # 只转根层条目（path 不含 "/"）：文件夹整体转（子树随之过来）、散文件直转
            roots = [f for f in files if f.fid and "/" not in f.path]
            for i in range(0, len(roots), 500):
                self._transfer_group(self._share_ctx, roots[i : i + 500], target, spec, result, on_log)
        on_progress(100)
        return result

    def _transfer_group(self, ctx: dict, group: list[ShareFile], target_dir: str, spec: TaskSpec, result: TransferResult, on_log) -> None:
        fsids = [int(f.fid) for f in group if str(f.fid).isdigit()]
        if not fsids:
            return
        # baidupcs_py 的 transfer 不带 app_id——它是网页内部接口，多传反而多一处暴露面
        params = {"shareid": ctx["shareid"], "from": ctx["uk"], "bdstoken": ctx["bdstoken"], "channel": "chunlei", "clienttype": 0, "web": 1}
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
            # rename 是写操作，连打最容易触发 -65：每条之间歇 0.5s（bdsavePro 同款间隔）
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
