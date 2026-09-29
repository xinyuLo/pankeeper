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
        is_vip = int(data.get("is_vip") or 0)
        vip_type = data.get("vip_type")
        if not is_vip:
            return {"name": "普通用户", "expires": None}
        name = {1: "VIP", 2: "SVIP", 4: "SVIP"}.get(int(vip_type or 0), "VIP")
        return {"name": name, "expires": None}

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
