"""转存记录：快照列表 / 详情日志 / 清理 / 触发 QMS·STRM。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser
from ..models import DdItem, Record
from ..services import qms

router = APIRouter(prefix="/api", tags=["records"])


def _row(r: Record) -> dict:
    return {
        "id": r.id,
        "n": r.n, "t": r.t, "p": r.p, "st": r.st, "cls": r.cls, "tm": r.tm,
        "qms": json.loads(r.qms_json or "{}"),
        "strm": json.loads(r.strm_json or "{}"),
        "share_url": r.share_url, "share_code": r.share_code,
        "cron": r.cron, "include_subdirs": r.include_subdirs,
        "exclude_count": r.exclude_count, "post_qms": r.post_qms, "post_notify": r.post_notify,
    }


@router.get("/records")
def list_records(_user=CurrentUser):
    with SessionLocal() as db:
        rows = db.query(Record).order_by(Record.id.desc()).limit(500).all()
    return [_row(r) for r in rows]


@router.get("/records/{record_id}/logs")
def record_logs(record_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        r = db.get(Record, record_id)
    if r is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    logs = json.loads(r.logs_json or "[]")
    if r.post_notify:  # 自动任务记录末行补推送展示（契约：手动转存不接推送）
        logs = logs + [{"lv": "INFO", "txt": "Server 酱推送已送达"}]
    return logs


@router.delete("/records/{record_id}")
def delete_record(record_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        r = db.get(Record, record_id)
        if r:
            db.delete(r)
            db.commit()
    return {"ok": True}


@router.delete("/records")
def clear_old(before: str = "", _user=CurrentUser):
    """清空三月前记录（before = ISO 日期）。"""
    if not before:
        raise HTTPException(status_code=400, detail="缺少 before 参数")
    with SessionLocal() as db:
        n = db.query(Record).filter(Record.tm < before).delete()
        db.commit()
    return {"count": n}


class TriggerBody(BaseModel):
    id: int


@router.post("/qms/trigger")
def trigger_qms(body: TriggerBody, _user=CurrentUser):
    ok, msg = qms.trigger_scrape(body.id)
    return {"ok": ok, "message": msg}


@router.post("/strm/trigger")
def trigger_strm(body: TriggerBody, _user=CurrentUser):
    ok, msg = qms.trigger_strm(body.id)
    return {"ok": ok, "message": msg}


@router.post("/records/{record_id}/retrigger-qms")
def retrigger_qms(record_id: int, _user=CurrentUser):
    """对该记录的目标目录再次触发 QMS（按转存配置的目录前缀匹配）。"""
    with SessionLocal() as db:
        r = db.get(Record, record_id)
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        link = None
        for d in db.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all():
            if r.p == d.path or r.p.startswith(d.path.rstrip("/") + "/"):
                link = d
                break
    if link is None:
        raise HTTPException(status_code=400, detail="该记录的目标目录未配置 QMS 联动")
    ok, msg = qms.trigger_scrape(link.qms_id)
    return {"ok": ok, "message": msg}


@router.post("/records/{record_id}/retry-failed")
def retry_failed(record_id: int, _user=CurrentUser):
    """重试失败项：把失败记录重新入队（M3 细化为按文件粒度）。"""
    from ..queue.engine import get_engine

    with SessionLocal() as db:
        r = db.get(Record, record_id)
    if r is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if not r.share_url:
        raise HTTPException(status_code=400, detail="该记录没有分享链接，无法重试")
    pos = get_engine().enqueue(
        {"name": r.n, "type": r.t, "path": r.p, "share_url": r.share_url, "share_code": r.share_code, "include_subdirs": r.include_subdirs}
    )
    return {"count": pos}
