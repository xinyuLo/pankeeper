from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser
from ..models import PushLog, Setting, now_str
from ..security import hash_password, verify_password
from ..services import notify, qms
from ..services.settings_svc import get_group, save_group

router = APIRouter(prefix="/api", tags=["settings"])

GROUPS = ("search", "notify", "qms", "media")

@router.get("/settings")
def get_settings(_user=CurrentUser):
    data = get_group("settings")

    if data["notify"].get("sendkey"):
        data["notify"]["sendkey"] = "****" + data["notify"]["sendkey"][-4:]
    if data["qms"].get("apikey"):
        data["qms"]["apikey"] = "****" + data["qms"]["apikey"][-4:]
    if data["qms"].get("tmdb_api_key"):
        data["qms"]["tmdb_api_key"] = "****" + data["qms"]["tmdb_api_key"][-4:]
    data["media"] = get_group("media")
    litepan = get_group("litepan")
    if litepan.get("apikey"):
        litepan["apikey"] = "****" + litepan["apikey"][-4:]
    data["litepan"] = litepan
    return data

class SecurityBody(BaseModel):
    username: str
    old_password: str = ""
    new_password: str = ""
    session_days: int | None = None

@router.put("/settings/security")
def put_security(body: SecurityBody, _user=CurrentUser):
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

class HealthBody(BaseModel):
    enabled: bool = True
    hour: int = 3
    minute: int = 0

@router.get("/settings/health")
def get_health(_user=CurrentUser):
    from ..services.healthcheck import get_cfg, next_run_at

    return {**get_cfg(), "next_run": next_run_at()}

@router.put("/settings/health")
def put_health(body: HealthBody, _user=CurrentUser):
    if not (0 <= body.hour <= 23) or not (0 <= body.minute <= 59):
        raise HTTPException(status_code=400, detail="时间不合法（hour 0-23 / minute 0-59）")
    current = get_group("health_cfg")
    current.update({"enabled": bool(body.enabled), "hour": int(body.hour), "minute": int(body.minute)})
    save_group("health_cfg", current)
    from ..services.healthcheck import reschedule

    reschedule()
    return {"ok": True}

@router.post("/settings/health/run")
def run_health_now(_user=CurrentUser):
    from ..services.healthcheck import run_check

    return run_check(force=True)

AVATAR_MAX_LEN = 700_000

@router.get("/settings/avatar")
def get_avatar(_user=CurrentUser):
    cfg = get_group("avatar_cfg")
    return {"data": cfg.get("data", ""), "updated": cfg.get("updated", "")}

@router.put("/settings/avatar")
def put_avatar(body: dict, _user=CurrentUser):
    data = str(body.get("data") or "").strip()
    if data and not data.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="只支持图片（data URL）")
    if len(data) > AVATAR_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"图片太大，请压到 {AVATAR_MAX_LEN // 1024}KB 以内")
    save_group("avatar_cfg", {"data": data, "updated": now_str()})
    return {"ok": True}

@router.delete("/settings/avatar")
def delete_avatar(_user=CurrentUser):
    save_group("avatar_cfg", {"data": "", "updated": ""})
    return {"ok": True}

@router.put("/settings/media")
def put_media_backend(body: dict, _user=CurrentUser):
    backend = (body or {}).get("backend")
    if backend not in ("qms", "litepan"):
        raise HTTPException(status_code=400, detail="未知联动后端")
    save_group("media", {"backend": backend})
    return {"ok": True}

@router.put("/settings/litepan")
def put_litepan(body: dict, _user=CurrentUser):
    body = body or {}
    save_group("litepan", {
        "enabled": bool(body.get("enabled")),
        "webhook_url": (body.get("webhook_url") or "").strip(),
        "apikey": (body.get("apikey") or "").strip(),
        "source": (body.get("source") or "").strip(),
    })
    return {"ok": True}

@router.get("/litepan/health")
def litepan_health(_user=CurrentUser):
    from ..services import litepan

    return litepan.litepan_health()

@router.post("/settings/litepan/test")
def test_litepan(body: dict, _user=CurrentUser):
    from ..services import litepan

    body = body or {}
    saved = get_group("litepan")
    url = (body.get("webhook_url") or "").strip() or (saved.get("webhook_url") or "")
    apikey = (body.get("apikey") or "").strip()

    if not apikey or apikey.startswith("****"):
        apikey = saved.get("apikey") or ""
    return litepan.test_webhook(url, apikey)

@router.put("/settings/{group}")
def put_settings(group: str, body: dict, _user=CurrentUser):
    if group not in GROUPS or group == "security":
        raise HTTPException(status_code=404, detail="未知配置组")
    current = get_group("settings")
    current[group] = body
    save_group("settings", current)
    return {"ok": True}

@router.post("/settings/search/test")
def test_search(body: dict, _user=CurrentUser):
    url = (body.get("url") or "").rstrip("/")
    import requests

    try:
        resp = requests.get(f"{url}/api/health", timeout=8)
        data = resp.json()

        from .search import _save_health_cache

        _save_health_cache(True, data.get("plugin_count"))
        return {"ok": True, "ms": int(resp.elapsed.total_seconds() * 1000), "plugins": data.get("plugin_count")}
    except (requests.RequestException, ValueError) as e:
        from .search import _save_health_cache

        _save_health_cache(False)
        return {"ok": False, "message": str(e)}

@router.post("/settings/notify/test")
def test_notify(body: dict, _user=CurrentUser):

    sendkey = (body.get("sendkey") or "").strip()
    if sendkey.startswith("****"):
        sendkey = get_group("settings")["notify"].get("sendkey") or ""
    ok, msg = notify.test_sendkey(sendkey)
    return {"ok": ok, "message": msg}

@router.get("/qms/health")
def qms_health(_user=CurrentUser):
    return qms.health()

@router.post("/settings/qms/test")
def test_qms(body: dict, _user=CurrentUser):

    apikey = (body.get("apikey") or "").strip()
    ok, msg = qms.test_connection(
        url=(body.get("url") or "").strip() or None,
        apikey=None if apikey.startswith("****") else (apikey or None),
    )
    return {"ok": ok, "message": msg}

@router.post("/settings/tmdb/test")
def test_tmdb(body: dict, _user=CurrentUser):
    from ..services import tmdb

    body = body or {}
    saved = get_group("settings")["qms"]
    apikey = (body.get("api_key") or "").strip()
    if not apikey or apikey.startswith("****"):
        apikey = saved.get("tmdb_api_key") or ""
    hosts = body.get("hosts")
    cfg = {
        "tmdb_api_key": apikey,
        "tmdb_mode": (body.get("mode") or "proxy").strip() or "proxy",
        "tmdb_proxy": (body.get("proxy") or "").strip(),
        "tmdb_hosts": hosts if isinstance(hosts, list) else [],
        "tmdb_skip_tls": bool(body.get("skip_tls")),
    }
    return tmdb.ping(cfg)

@router.delete("/notify/history")
def clear_push_history(before: str = "", _user=CurrentUser):
    with SessionLocal() as db:
        q = db.query(PushLog)
        if before:
            q = q.filter(PushLog.ts < before)
        n = q.delete(synchronize_session=False)
        db.commit()
    return {"count": n}

@router.get("/notify/history")
def push_history(limit: int = 50, _user=CurrentUser):
    limit = max(1, min(limit, 200))
    with SessionLocal() as db:
        rows = db.query(PushLog).order_by(PushLog.id.desc()).limit(limit).all()
        items = [
            {
                "id": r.id, "ts": r.ts, "title": r.title, "kind": r.kind,
                "status": r.status, "error": r.error or "",
                "content": (r.content or "")[:200],
                "has_more": bool(r.content and len(r.content) > 200),
            }
            for r in rows
        ]
    return {
        "items": items,
        "delivered": sum(1 for i in items if i["status"] == "success"),
        "failed": sum(1 for i in items if i["status"] == "fail"),
    }

@router.get("/notify/history/{log_id}")
def push_history_detail(log_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        r = db.get(PushLog, log_id)
        if r is None:
            raise HTTPException(status_code=404, detail="推送记录不存在")
        return {
            "id": r.id, "ts": r.ts, "title": r.title, "kind": r.kind,
            "status": r.status, "error": r.error or "", "content": r.content or "",
        }

