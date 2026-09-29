"""百度网盘最小客户端（原创实现）。

当前范围：凭据验证 + 容量/会员摘要（转存链路在 M3）。
接口均为百度网盘网页端公开接口，社区工具（BaiduPCS 系列）广泛使用：
- GET /api/quota?checkfree=1&checkexpire=1      容量（total/used 字节），兼做 Cookie 探活
- GET /rest/2.0/xpan/nas?method=uinfo           用户信息（is_vip / vip_type 等）
解析全部做防御性处理：字段缺失返回 None，不让摘要挂掉。
"""
from __future__ import annotations

import httpx

from ..security import decrypt_credential
from ..services import reqstat
from .base import AdapterError, CredentialExpired
from .rate_gate import RateGate

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class BaiduClient:
    type = "baidu"

    def __init__(self, cookies_enc: str, gate: RateGate | None = None):
        self.cookies = decrypt_credential(cookies_enc)
        self.gate = gate or RateGate("baidu", min_interval=1.0, cooldown=30.0)
        self._http = httpx.Client(
            headers={"cookie": self.cookies, "user-agent": UA},
            timeout=15.0,
            # 请求计数（网盘日志页 / 风控预警）：挂在传输层，业务方法零侵入
            event_hooks={"request": [reqstat.hook("baidu")]},
        )

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
