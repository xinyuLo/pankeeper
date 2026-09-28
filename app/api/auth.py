"""认证：单管理员登录换 JWT。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..models import Setting
from ..security import hash_password, make_token, verify_password
from ..services.settings_svc import get_group

router = APIRouter(prefix="/api/auth", tags=["auth"])

ADMIN_KEY = "admin"
DEFAULT_PWD = "12345678"  # 与前端登录页预填一致（原型同款）


def ensure_admin() -> None:
    with SessionLocal() as s:
        if s.get(Setting, ADMIN_KEY) is None:
            s.add(Setting(key=ADMIN_KEY, value_json=json.dumps({"pwd_hash": hash_password(DEFAULT_PWD)})))
            s.commit()
    print(f"[init] 默认管理员已创建：admin / {DEFAULT_PWD}（请尽快在「系统设置 → 账号安全」修改）")


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody):
    sec = get_group("settings")["security"]
    if body.username != sec.get("username", "admin"):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    with SessionLocal() as s:
        row = s.get(Setting, ADMIN_KEY)
    pwd_hash = json.loads(row.value_json).get("pwd_hash", "") if row else ""
    if not verify_password(body.password, pwd_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    days = int(sec.get("session_days") or 7)
    return {"token": make_token(body.username, days), "username": body.username}
