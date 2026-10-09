from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..services import qms

router = APIRouter(prefix="/api/qms", tags=["qms"])

_MEDIA_TYPE_CN = {"movie": "电影", "tvshow": "剧集", "tv": "剧集"}

@router.get("/scrape-pathes")
def qms_scrape_pathes(_user=CurrentUser):
    rows = qms.scrape_pathes()
    if rows is None:
        raise HTTPException(status_code=400, detail="QMS 未启用或连接失败")
    return [
        {
            "id": r.get("id"),
            "media_type": _MEDIA_TYPE_CN.get(str(r.get("media_type") or ""), str(r.get("media_type") or "")),
            "source_path": r.get("source_path"),
        }
        for r in rows
    ]

@router.get("/sync-pathes")
def qms_sync_pathes(_user=CurrentUser):
    rows = qms.sync_pathes()
    if rows is None:
        raise HTTPException(status_code=400, detail="QMS 未启用或连接失败")
    return [
        {
            "id": r.get("id"),
            "remote_path": r.get("remote_path") or r.get("local_path"),
        }
        for r in rows
    ]
