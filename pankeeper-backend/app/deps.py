"""FastAPI 依赖：登录态校验。"""
from __future__ import annotations

import os

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as OrmSession

from .db import SessionLocal
from .models import Account
from .security import decrypt_credential, parse_token

# ---- 本机免登录 ----------------------------------------------------------
# 只有「来源 IP 是本机回环」时才免票：本地开发时直接开 /docs、curl 接口不用
# 先去登录页捞 token；局域网/外网的访问者一律要 token。
# 注意：Docker 部署时容器收到的来源 IP 是网桥地址（172.x），不是 127.0.0.1，
# 因此豁免在容器里天然失效 —— 这是期望行为。若前面挂了反代且反代以回环连过来，
# 可用环境变量 PK_LOCAL_BYPASS=0 彻底关掉豁免。
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOCAL_BYPASS = os.getenv("PK_LOCAL_BYPASS", "1").strip().lower() not in ("0", "false", "no", "off")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_local_request(request: Request) -> bool:
    """来源是否为本机回环（且未通过环境变量关闭豁免）。"""
    if not LOCAL_BYPASS:
        return False
    host = request.client.host if request.client else ""
    return host in _LOCAL_HOSTS


def get_current_user(request: Request) -> str:
    if is_local_request(request):
        return "local"
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    username = parse_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="登录凭证不存在或已过期")
    return username


CurrentUser = Depends(get_current_user)


def make_adapter_for(db: OrmSession, drive_type: str, acc_id: int | None = None):
    """按账号构造 adapter 实例（Cookie 解密）。未配置抛 400。

    acc_id 缺省时回落该类型第一个账号 —— 供尚未接账号维度的调用方平滑过渡。
    """
    from .adapters.baidu import BaiduClient
    from .adapters.base import AdapterError
    from .adapters.quark import QuarkAdapter

    if acc_id is not None:
        acc = db.get(Account, acc_id)
        if acc is None or acc.type != drive_type:
            raise HTTPException(status_code=404, detail="账号不存在")
    else:
        acc = db.query(Account).filter(Account.type == drive_type).order_by(Account.id).first()
    # 只看 cookies_enc：status 是"最近一次验证的结果"，新增账号保存即验证时
    # status 还是建号默认的 unset，凭据其实已写入，不能拿来当"未配置"判据
    if acc is None or not acc.cookies_enc:
        raise HTTPException(status_code=400, detail=f"{drive_type} 账号未配置凭据")
    if acc.type == "quark":
        return QuarkAdapter(acc.cookies_enc)
    if acc.type == "baidu":
        return BaiduClient(acc.cookies_enc)
    raise HTTPException(status_code=400, detail=f"网盘 {acc.type} 适配器尚未实现")


__all__ = [
    "get_current_user",
    "CurrentUser",
    "get_db",
    "is_local_request",
    "make_adapter_for",
    "httpx",
    "decrypt_credential",
]
