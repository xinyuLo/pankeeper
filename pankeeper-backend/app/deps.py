from __future__ import annotations

import os

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as OrmSession

from .db import SessionLocal
from .models import Account
from .security import decrypt_credential, parse_token

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOCAL_BYPASS = os.getenv("PK_LOCAL_BYPASS", "1").strip().lower() not in ("0", "false", "no", "off")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def is_local_request(request: Request) -> bool:
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
    from .adapters.baidu import BaiduClient
    from .adapters.base import AdapterError
    from .adapters.pan115 import Pan115Adapter
    from .adapters.quark import QuarkAdapter

    if acc_id is not None:
        acc = db.get(Account, acc_id)
        if acc is None or acc.type != drive_type:
            raise HTTPException(status_code=404, detail="账号不存在")
    else:
        acc = db.query(Account).filter(Account.type == drive_type).order_by(Account.id).first()

    if acc is None or not acc.cookies_enc:
        raise HTTPException(status_code=400, detail=f"{drive_type} 账号未配置凭据")
    if acc.type == "quark":
        return QuarkAdapter(acc.cookies_enc)
    if acc.type == "baidu":
        return BaiduClient(acc.cookies_enc)
    if acc.type == "115":
        return Pan115Adapter(acc.cookies_enc)
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
