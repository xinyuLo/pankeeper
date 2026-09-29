"""搜索：pansou 代理 + 频道/地址展示。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..services import pansou

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/results")
def search_results(kw: str, refresh: bool = False, _user=CurrentUser):
    if not kw.strip():
        return []
    try:
        return pansou.search(kw.strip(), refresh=refresh)
    except pansou.PanSouError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/pansou-addr")
def pansou_addr(_user=CurrentUser):
    from ..services.settings_svc import get_group

    return get_group("settings")["search"]["pansou_url"]


@router.get("/health")
def engine_health(_user=CurrentUser):
    """检索引擎健康度（透传 pansou /api/health，不含任何地址信息）。"""
    h = pansou.health()
    return {"ok": h.get("ok", False), "plugins": h.get("plugins"), "channels": h.get("channels")}


@router.get("/channels")
def search_channels(_user=CurrentUser):
    """频道清单 + 选中状态：on = 白名单为空（全用）或该频道在白名单里。"""
    from ..services.settings_svc import get_group

    selected = get_group("settings")["search"].get("channels") or []
    return [{"name": c, "on": (not selected) or (c in selected)} for c in pansou.channels()]
