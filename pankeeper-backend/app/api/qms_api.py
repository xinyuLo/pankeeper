"""QMS 联动查询：把 qMediaSync 的刮削路径 / STRM 同步路径列表透传给前端。

转存配置页的「联动 QMS / 生成 STRM」下拉数据源。鉴权用已保存的 QMS url + X-API-Key。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..services import qms

router = APIRouter(prefix="/api/qms", tags=["qms"])

_MEDIA_TYPE_CN = {"movie": "电影", "tvshow": "剧集", "tv": "剧集"}


@router.get("/scrape-pathes")
def qms_scrape_pathes(_user=CurrentUser):
    """QMS 刮削路径列表（转存配置页「联动 QMS」下拉）。"""
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
    """QMS STRM 同步路径列表（转存配置页「生成 STRM」下拉）。"""
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
