from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..models import SearchHistory
from ..services import pansou

router = APIRouter(prefix="/api/search", tags=["search"])

@router.get("/results")
def search_results(kw: str, refresh: bool = False, _user=CurrentUser):
    if not kw.strip():
        return []
    try:
        rows = pansou.search(kw.strip(), refresh=refresh)
    except pansou.PanSouError as e:
        raise HTTPException(status_code=502, detail=str(e))

    _log_keyword(kw.strip())
    return rows

def _log_keyword(kw: str) -> None:
    import time as _time

    from ..db import SessionLocal

    try:
        with SessionLocal() as db:
            db.add(SearchHistory(keyword=kw[:60], fetched_at=int(_time.time())))
            db.commit()
    except Exception:
        pass

@router.get("/recent-keywords")
def recent_keywords(limit: int = 5, _user=CurrentUser):
    limit = max(1, min(limit, 10))
    from sqlalchemy import func

    from ..db import SessionLocal

    with SessionLocal() as db:
        rows = (
            db.query(SearchHistory.keyword, func.max(SearchHistory.fetched_at).label("latest"))
            .group_by(SearchHistory.keyword)
            .limit(limit)
            .all()
        )

    rows.sort(key=lambda r: r[1], reverse=True)
    return [r[0] for r in rows]

@router.get("/pansou-addr")
def pansou_addr(_user=CurrentUser):
    from ..services.settings_svc import get_group

    return get_group("settings")["search"]["pansou_url"]

def _pansou_base() -> str:
    from ..services.settings_svc import get_group

    return (get_group("settings")["search"]["pansou_url"] or "").rstrip("/")

@router.get("/health")
def engine_health(_user=CurrentUser):
    h = pansou.health()
    ok = h.get("ok", False)
    _save_health_cache(ok, ok and h.get("plugins"), h.get("channels"))
    return {"ok": ok, "plugins": h.get("plugins"), "channels": h.get("channels")}

@router.get("/health-cached")
def engine_health_cached(_user=CurrentUser):
    from ..services.settings_svc import get_group

    h = get_group("pansou_health")
    return {"ok": h.get("ok"), "plugins": h.get("plugins"), "checked_at": h.get("checked_at", "")}

def _save_health_cache(ok: bool, plugins=None, channels=None) -> None:
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
    except Exception:
        pass

@router.get("/channels")
def search_channels(_user=CurrentUser):
    from ..services.settings_svc import get_group

    selected = get_group("settings")["search"].get("channels") or []
    return [{"name": c, "on": (not selected) or (c in selected)} for c in pansou.channels()]

_PROBE_TTL = 5 * 60
_PROBE_OK: dict[tuple, float] = {}

@router.post("/check-link")
def check_link(body: dict, _user=CurrentUser):
    t = (body.get("type") or "").strip()
    url = (body.get("url") or "").strip()
    code = (body.get("share_code") or "").strip()
    if not url:
        return {"state": "unknown", "summary": "无链接"}

    from ..services.share_cache import share_key, share_list_cache

    hit = share_list_cache.get(share_key(t, url, code))
    if hit and isinstance(hit[0], dict) and (hit[0].get("total") or 0) > 0:
        return {"state": "ok", "summary": "近期查看过文件清单", "from_cache": True}

    probe_key = (t, url, code)
    ts = _PROBE_OK.get(probe_key)
    if ts and time.time() - ts < _PROBE_TTL:
        return {"state": "ok", "summary": "5 分钟内检测过", "from_cache": True}

    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..adapters.factory import make_adapter

    try:
        adapter = make_adapter(t)
    except AdapterError as e:
        return {"state": "unknown", "summary": str(e)}
    DEAD_HINTS = ("不存在", "已取消", "已删除", "过期", "失效", "违规", "敏感")
    try:
        spec = TaskSpec(share_url=url, share_code=code, include_subdirs=False)
        probe = getattr(adapter, "probe_share", None)
        files = probe(spec) if probe is not None else None
        if files is None:
            files = adapter.list_share(spec)
        _PROBE_OK[probe_key] = time.time()
        if not files:
            return {"state": "bad", "summary": "分享内容为空（可能已失效）"}
        return {"state": "ok", "summary": f"清单 {len(files)} 项"}
    except ShareBanned as e:
        return {"state": "bad", "summary": str(e) or "分享已失效"}
    except CredentialExpired:
        return {"state": "unknown", "summary": "网盘凭据已过期"}
    except AdapterError as e:
        msg = str(e)
        if any(k in msg for k in DEAD_HINTS):
            return {"state": "bad", "summary": msg}
        return {"state": "uncertain", "summary": msg[:60]}
    except Exception as e:
        return {"state": "unknown", "summary": str(e)[:60] or "检测异常"}

@router.get("/share-files")
def search_share_files(type: str, url: str, code: str = "", refresh: bool = False, _user=CurrentUser):
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..adapters.factory import make_adapter
    from ..services.share_cache import build_payload, share_key, share_list_cache

    if not url:
        raise HTTPException(status_code=400, detail="缺少分享链接")
    try:
        adapter = make_adapter(type)
    except AdapterError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def _live() -> dict:
        files = adapter.list_share(TaskSpec(share_url=url, share_code=code, include_subdirs=True))
        if not files:

            raise ShareBanned("分享内容为空（0 个文件），链接可能已失效")
        return build_payload(files)

    try:
        payload, cached_at, pulled = share_list_cache.get_or_load(share_key(type, url, code), _live, refresh=refresh)
    except ShareBanned as e:
        raise HTTPException(status_code=410, detail=f"分享已失效：{e}")
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "total": len(payload["files"]),
        "files": payload["files"],
        "tree": payload["tree"],
        "cached_at": int(cached_at),
        "fresh": pulled,
    }
