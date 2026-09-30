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
    """检索引擎健康度（透传 pansou /api/health，不含任何地址信息）。结果写进缓存。"""
    h = pansou.health()
    ok = h.get("ok", False)
    _save_health_cache(ok, ok and h.get("plugins"), h.get("channels"))
    return {"ok": ok, "plugins": h.get("plugins"), "channels": h.get("channels")}


@router.get("/health-cached")
def engine_health_cached(_user=CurrentUser):
    """上一次探测的缓存状态（每日探活/页面测试时刷新）。首屏渲染用，不现场打网盘。"""
    from ..services.settings_svc import get_group

    h = get_group("pansou_health")
    return {"ok": h.get("ok"), "plugins": h.get("plugins"), "checked_at": h.get("checked_at", "")}


def _save_health_cache(ok: bool, plugins=None, channels=None) -> None:
    """把 PanSou 在线状态落进 settings（pansou_health 组）。失败吞掉，不影响主流程。"""
    try:
        from datetime import datetime

        from ..services.settings_svc import save_group

        save_group(
            "pansou_health",
            {
                "ok": bool(ok),
                "plugins": plugins,
                "channels": channels,
                "checked_at": datetime.now().strftime("%m-%d %H:%M"),
            },
        )
    except Exception:  # noqa: BLE001
        pass


@router.get("/channels")
def search_channels(_user=CurrentUser):
    """频道清单 + 选中状态：on = 白名单为空（全用）或该频道在白名单里。"""
    from ..services.settings_svc import get_group

    selected = get_group("settings")["search"].get("channels") or []
    return [{"name": c, "on": (not selected) or (c in selected)} for c in pansou.channels()]
