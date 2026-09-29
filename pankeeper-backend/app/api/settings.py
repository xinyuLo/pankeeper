"""系统设置：四组配置读写 + 连通性测试 + 推送历史 + 改密码。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..models import Setting
from ..security import hash_password, verify_password
from ..services import notify, qms
from ..services.settings_svc import get_group, save_group

router = APIRouter(prefix="/api", tags=["settings"])

GROUPS = ("search", "notify", "qms", "security")


@router.get("/settings")
def get_settings(_user: str = ""):
    data = get_group("settings")
    # sendkey/apikey 只回掩码，绝不回明文（契约：值为 **** 开头 = 前端没改，保存时保留旧值）
    if data["notify"].get("sendkey"):
        data["notify"]["sendkey"] = "****" + data["notify"]["sendkey"][-4:]
    if data["qms"].get("apikey"):
        data["qms"]["apikey"] = "****" + data["qms"]["apikey"][-4:]
    return data


class SecurityBody(BaseModel):
    username: str
    old_password: str = ""
    new_password: str = ""
    session_days: int | None = None


@router.put("/settings/security")
def put_security(body: SecurityBody, _user: str = ""):
    """⚠️ 必须注册在 /settings/{group} 之前，否则会被通配路由吃掉（已按顺序排放）。"""
    current = get_group("settings")
    current["security"]["username"] = body.username or "admin"
    if body.session_days:
        current["security"]["session_days"] = body.session_days
    save_group("settings", current)
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(status_code=400, detail="新密码至少 8 位")
        with SessionLocal() as s:
            row = s.get(Setting, "admin")
            stored = json.loads(row.value_json) if row else {}
            if not verify_password(body.old_password, stored.get("pwd_hash", "")):
                raise HTTPException(status_code=400, detail="当前密码错误")
            stored["pwd_hash"] = hash_password(body.new_password)
            row.value_json = json.dumps(stored)
            s.commit()
    return {"ok": True}


@router.put("/settings/{group}")
def put_settings(group: str, body: dict, _user: str = ""):
    if group not in GROUPS or group == "security":  # security 走上面的专属端点
        raise HTTPException(status_code=404, detail="未知配置组")
    current = get_group("settings")
    current[group] = body
    save_group("settings", current)
    return {"ok": True}


@router.post("/settings/search/test")
def test_search(body: dict):
    """测 pansou 连通：直接打它 /api/health（用 requests，理由同 pansou.py 模块注释）。"""
    url = (body.get("url") or "").rstrip("/")
    import requests

    try:
        resp = requests.get(f"{url}/api/health", timeout=8)
        data = resp.json()
        return {"ok": True, "ms": int(resp.elapsed.total_seconds() * 1000), "plugins": data.get("plugin_count")}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "message": str(e)}


@router.post("/settings/notify/test")
def test_notify(body: dict):
    ok, msg = notify.test_sendkey(body.get("sendkey") or "")
    return {"ok": ok, "message": msg}


@router.post("/settings/qms/test")
def test_qms(body: dict):
    ok, msg = qms.test_connection()
    return {"ok": ok, "message": msg}


@router.get("/notify/history")
def push_history(_user: str = ""):
    # M1：推送即时发送不落库；历史明细挂到 push_log 表后再补
    return {"delivered": 0, "failed": 0}


