"""FastAPI 依赖：登录态校验。"""
from __future__ import annotations

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as OrmSession

from .db import SessionLocal
from .models import Account
from .security import decrypt_credential, parse_token


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    username = parse_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="登录凭证不存在或已过期")
    return username


CurrentUser = Depends(get_current_user)


def make_adapter_for(db: OrmSession, drive_type: str):
    """按网盘类型构造 adapter 实例（Cookie 解密）。未配置抛 400。"""
    from .adapters.baidu import BaiduClient
    from .adapters.base import AdapterError
    from .adapters.quark import QuarkAdapter

    acc = db.get(Account, drive_type)
    if acc is None or not acc.cookies_enc:
        raise HTTPException(status_code=400, detail=f"{drive_type} 账号未配置凭据")
    if drive_type == "quark":
        return QuarkAdapter(acc.cookies_enc)
    if drive_type == "baidu":
        return BaiduClient(acc.cookies_enc)
    raise HTTPException(status_code=400, detail=f"网盘 {drive_type} 适配器尚未实现")


__all__ = ["get_current_user", "CurrentUser", "get_db", "make_adapter_for", "httpx", "decrypt_credential"]
