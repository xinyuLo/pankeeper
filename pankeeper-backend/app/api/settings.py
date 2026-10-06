"""系统设置：四组配置读写 + 连通性测试 + 推送历史 + 改密码。"""
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
    # sendkey/apikey 只回掩码，绝不回明文（契约：值为 **** 开头 = 前端没改，保存时保留旧值）
    if data["notify"].get("sendkey"):
        data["notify"]["sendkey"] = "****" + data["notify"]["sendkey"][-4:]
    if data["qms"].get("apikey"):
        data["qms"]["apikey"] = "****" + data["qms"]["apikey"][-4:]
    if data["qms"].get("tmdb_api_key"):
        data["qms"]["tmdb_api_key"] = "****" + data["qms"]["tmdb_api_key"][-4:]
    data["media"] = get_group("media")  # 联动后端选择（qms/litepan）
    litepan = get_group("litepan")  # LitePan 对接参数（联动后端=litepan 时用）
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


class HealthBody(BaseModel):
    enabled: bool = True
    hour: int = 3
    minute: int = 0


@router.get("/settings/health")
def get_health(_user=CurrentUser):
    """网盘凭据每日探活配置，附带下次执行时间。"""
    from ..services.healthcheck import get_cfg, next_run_at

    return {**get_cfg(), "next_run": next_run_at()}


@router.put("/settings/health")
def put_health(body: HealthBody, _user=CurrentUser):
    """保存探活配置并立即重排。
    ⚠️ 与 security 同理，必须注册在 /settings/{group} 之前，否则被通配路由吃掉。"""
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
    """立刻手动跑一次探活（忽略 enabled 开关），用于验证配置能否连通。"""
    from ..services.healthcheck import run_check

    return run_check(force=True)


# ===== 头像 =====
# data URL 长度上限（前端已压到 256×256 JPEG，正常远低于此）
AVATAR_MAX_LEN = 700_000


@router.get("/settings/avatar")
def get_avatar(_user=CurrentUser):
    """取头像（data URL）。单独接口——不并进 /settings 响应里，免得每次拉配置都背着它。"""
    cfg = get_group("avatar_cfg")
    return {"data": cfg.get("data", ""), "updated": cfg.get("updated", "")}


@router.put("/settings/avatar")
def put_avatar(body: dict, _user=CurrentUser):
    """保存头像。只接受 data:image/... 开头的 data URL（压缩在前端做）。"""
    data = str(body.get("data") or "").strip()
    if data and not data.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="只支持图片（data URL）")
    if len(data) > AVATAR_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"图片太大，请压到 {AVATAR_MAX_LEN // 1024}KB 以内")
    save_group("avatar_cfg", {"data": data, "updated": now_str()})
    return {"ok": True}


@router.delete("/settings/avatar")
def delete_avatar(_user=CurrentUser):
    """移除头像，前端回落到用户名首字。"""
    save_group("avatar_cfg", {"data": "", "updated": ""})
    return {"ok": True}


@router.put("/settings/media")
def put_media_backend(body: dict, _user=CurrentUser):
    """联动后端选择（qms/litepan）：独立顶层配置组，别并进 settings 子组。"""
    backend = (body or {}).get("backend")
    if backend not in ("qms", "litepan"):
        raise HTTPException(status_code=400, detail="未知联动后端")
    save_group("media", {"backend": backend})
    return {"ok": True}


@router.put("/settings/litepan")
def put_litepan(body: dict, _user=CurrentUser):
    """LitePan 对接参数（HTTP Webhook）：独立顶层配置组（同 media），别并进 settings 子组。
    掩码 apikey 由 save_group 的 litepan 分支保留旧值。"""
    body = body or {}
    save_group("litepan", {
        "webhook_url": (body.get("webhook_url") or "").strip(),
        "apikey": (body.get("apikey") or "").strip(),
        "event": (body.get("event") or "").strip(),
    })
    return {"ok": True}


@router.post("/settings/litepan/test")
def test_litepan(body: dict, _user=CurrentUser):
    """打一次真实 webhook 验证地址+密钥+连通（测试事件不会命中正常规则）。
    掩码 apikey（****开头）= 没改 → 用库里已保存的密钥，别把掩码当真 key 发。"""
    from ..services import litepan

    body = body or {}
    apikey = (body.get("apikey") or "").strip()
    if apikey.startswith("****"):
        apikey = get_group("litepan").get("apikey") or ""
    return litepan.test_webhook(body.get("webhook_url") or "", apikey)


@router.put("/settings/{group}")
def put_settings(group: str, body: dict, _user=CurrentUser):
    if group not in GROUPS or group == "security":  # security 走上面的专属端点
        raise HTTPException(status_code=404, detail="未知配置组")
    current = get_group("settings")
    current[group] = body
    save_group("settings", current)
    return {"ok": True}


@router.post("/settings/search/test")
def test_search(body: dict, _user=CurrentUser):
    """测 pansou 连通：直接打它 /api/health（用 requests，理由同 pansou.py 模块注释）。"""
    url = (body.get("url") or "").rstrip("/")
    import requests

    try:
        resp = requests.get(f"{url}/api/health", timeout=8)
        data = resp.json()
        # 测试结果同步进 pansou_health 缓存：搜索页首屏状态保持新鲜
        from .search import _save_health_cache

        _save_health_cache(True, data.get("plugin_count"))
        return {"ok": True, "ms": int(resp.elapsed.total_seconds() * 1000), "plugins": data.get("plugin_count")}
    except (requests.RequestException, ValueError) as e:
        from .search import _save_health_cache

        _save_health_cache(False)
        return {"ok": False, "message": str(e)}


@router.post("/settings/notify/test")
def test_notify(body: dict, _user=CurrentUser):
    # 掩码值（****开头）= 没改 → 用库里已保存的 sendkey 测，别把掩码当真 key 发
    sendkey = (body.get("sendkey") or "").strip()
    if sendkey.startswith("****"):
        sendkey = get_group("settings")["notify"].get("sendkey") or ""
    ok, msg = notify.test_sendkey(sendkey)
    return {"ok": ok, "message": msg}


@router.get("/qms/health")
def qms_health(_user=CurrentUser):
    """QMS 引擎状态胶囊（设置页）：语义同 /search/health。"""
    return qms.health()


@router.post("/settings/qms/test")
def test_qms(body: dict, _user=CurrentUser):
    # 用传入的 url/apikey（输入框正在编辑的值）测，未传才回落到已保存配置——与 pansou 的测试语义一致。
    # 掩码值（****开头）等于"没改"：不能当真 key 发出去（QMS 会 401），回落已保存的。
    apikey = (body.get("apikey") or "").strip()
    ok, msg = qms.test_connection(
        url=(body.get("url") or "").strip() or None,
        apikey=None if apikey.startswith("****") else (apikey or None),
    )
    return {"ok": ok, "message": msg}


@router.get("/notify/history")
def push_history(limit: int = 50, _user=CurrentUser):
    """推送历史明细（推送历史页数据源）：时间/标题/成败/失败原因，按时间倒序。

    delivered/failed 是本页窗口内的计数（旧设置页按钮只回计数，字段保留兼容）。
    content 只回前 200 字预览（列表页够用，正文可能几十 KB）——完整正文走 /notify/history/{id}。"""
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
    """单条推送的完整正文（推送历史「详情」弹窗用）。"""
    with SessionLocal() as db:
        r = db.get(PushLog, log_id)
        if r is None:
            raise HTTPException(status_code=404, detail="推送记录不存在")
        return {
            "id": r.id, "ts": r.ts, "title": r.title, "kind": r.kind,
            "status": r.status, "error": r.error or "", "content": r.content or "",
        }


