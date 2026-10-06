"""认证：单管理员登录换 JWT。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import SessionLocal
from ..models import Setting
from ..security import hash_password, make_token, verify_password
from ..services.settings_svc import get_group

router = APIRouter(prefix="/api/auth", tags=["auth"])

ADMIN_KEY = "admin"
DEFAULT_PWD = "admin#123"  # 与前端登录页预填一致

# ---- 登录防暴力破解：同来源连续失败 N 次锁定一段时间（内存计数，重启即清） ----
LOCK_AFTER = 5
LOCK_SECONDS = 900  # 15 分钟
_attempts: dict[str, dict] = {}


def _client_key(request: Request) -> str:
    # 注意：若有反代，这里拿到的是代理 IP；家用单机直连场景足够。
    # 上公网/套 CDN 时应改读 X-Forwarded-For 最左段。
    return request.client.host if request.client else "unknown"


def _locked_seconds(key: str) -> int:
    st = _attempts.get(key)
    if not st:
        return 0
    remain = int(st["until"] - time.time())
    return max(0, remain)


def _register_fail(key: str) -> int:
    st = _attempts.setdefault(key, {"n": 0, "until": 0.0})
    st["n"] += 1
    if st["n"] >= LOCK_AFTER:
        st["until"] = time.time() + LOCK_SECONDS
        return LOCK_SECONDS
    return 0


def _clear(key: str) -> None:
    _attempts.pop(key, None)


def ensure_admin() -> None:
    with SessionLocal() as s:
        if s.get(Setting, ADMIN_KEY) is None:
            s.add(Setting(key=ADMIN_KEY, value_json=json.dumps({"pwd_hash": hash_password(DEFAULT_PWD)})))
            s.commit()
            # 文案在守卫内：账号已存在时别再喊"已创建"吓人（2026-10-06 NAS 部署时实拍误导）
            print(f"[init] 默认管理员已创建：admin / {DEFAULT_PWD}（请尽快在「系统设置 → 账号安全」修改）")


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request):
    key = _client_key(request)
    remain = _locked_seconds(key)
    if remain > 0:
        # 429：明确告诉前端是「试太多次」，不是密码错
        raise HTTPException(status_code=429, detail=f"失败次数过多，请 {remain // 60 + 1} 分钟后再试")

    def fail() -> HTTPException:
        locked = _register_fail(key)
        detail = "用户名或密码错误"
        if locked:
            detail = f"失败次数过多，已锁定 {locked // 60} 分钟"
        return HTTPException(status_code=401, detail=detail)

    sec = get_group("settings")["security"]
    if body.username != sec.get("username", "admin"):
        raise fail()
    with SessionLocal() as s:
        row = s.get(Setting, ADMIN_KEY)
    pwd_hash = json.loads(row.value_json).get("pwd_hash", "") if row else ""
    if not verify_password(body.password, pwd_hash):
        raise fail()
    _clear(key)
    days = int(sec.get("session_days") or 7)
    return {"token": make_token(body.username, days), "username": body.username}
