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

        "backend": (r.backend or "").strip(),
        "share_url": r.share_url, "share_code": r.share_code,
        "cron": r.cron, "include_subdirs": r.include_subdirs,
        "exclude_count": r.exclude_count, "post_qms": r.post_qms, "post_notify": r.post_notify,
        "files": json.loads(r.files_json or "[]"),
    }

@router.get("/records")
def list_records(source: str = "search", _user=CurrentUser):
    with SessionLocal() as db:
        q = db.query(Record)
        if source:
            q = q.filter(Record.source == source)
        rows = q.order_by(Record.id.desc()).limit(500).all()
    return [_row(r) for r in rows]

@router.get("/records/{record_id}/logs")
def record_logs(record_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        r = db.get(Record, record_id)
    if r is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    logs = json.loads(r.logs_json or "[]")
    if r.post_notify:
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
    with SessionLocal() as db:
        q = db.query(Record).filter(Record.source == "search")
        if before:
            q = q.filter(Record.tm < before)
        n = q.delete(synchronize_session=False)
        db.commit()
    return {"count": n}

@router.get("/records/{record_id}/share-files")
def record_share_files(record_id: int, refresh: bool = False, _user=CurrentUser):
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..deps import make_adapter_for
    from ..services.share_cache import build_payload, share_key, share_list_cache

    db = SessionLocal()
    try:
        r = db.get(Record, record_id)
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        if not r.share_url:
            raise HTTPException(status_code=400, detail="该记录没有分享链接")
        try:
            adapter = make_adapter_for(db, r.t, None)
        except HTTPException:
            db.close()
            raise

        key = share_key(r.t, r.share_url, r.share_code)

        def _live() -> dict:
            files = adapter.list_share(TaskSpec(share_url=r.share_url, share_code=r.share_code, include_subdirs=True))
            if not files:

                raise ShareBanned("分享内容为空（0 个文件），链接可能已失效")
            return build_payload(files)

        try:
            payload, cached_at, pulled = share_list_cache.get_or_load(key, _live, refresh=refresh)
        except ShareBanned as e:
            raise HTTPException(status_code=410, detail=f"分享已失效：{e}")
        except CredentialExpired as e:
            raise HTTPException(status_code=401, detail=str(e))
        except AdapterError as e:
            raise HTTPException(status_code=502, detail=str(e))
    finally:
        db.close()
    return {
        "total": len(payload["files"]),
        "files": payload["files"],
        "tree": payload["tree"],
        "cached_at": int(cached_at),
        "fresh": pulled,
    }

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

@router.post("/records/{record_id}/retrigger")
def retrigger_record(record_id: int, _user=CurrentUser):
    import json as _json

    from ..services import litepan as lp_svc
    from ..services.settings_svc import get_group
    from ..transfer.auto import resolve_litepan_link

    with SessionLocal() as db:
        r = db.get(Record, record_id)
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        p, name, drive = r.p or "", r.n or "", r.t or ""
        r_backend = (r.backend or "").strip()
        files = [
            {"name": e.get("name")}
            for e in (json.loads(r.files_json or "[]") if r.files_json else [])
            if isinstance(e, dict) and e.get("name")
        ][:20]
        share_url, share_code = r.share_url or "", r.share_code or ""

    backend = (r_backend or "").strip() or get_group("media").get("backend", "qms")
    if backend == "litepan":
        link = resolve_litepan_link(p)
        if link is None:
            raise HTTPException(status_code=400, detail="该记录的目标目录未配置 LitePan 联动")
        res = lp_svc.notify_transfer_done({
            "drive": drive, "task": name, "path": p,
            "files": files, "share_url": share_url, "share_code": share_code,
            "event": link["event"],
        })
        return {"ok": res["ok"], "message": res["message"]}

    with SessionLocal() as db:
        link = None
        for d in db.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all():
            if p == d.path or p.startswith(d.path.rstrip("/") + "/"):
                link = d
                break
    if link is None:
        raise HTTPException(status_code=400, detail="该记录的目标目录未配置 QMS 联动")
    ok, msg = qms.trigger_scrape(link.qms_id)
    return {"ok": ok, "message": msg or "已触发 QMS 刮削"}

@router.post("/records/{record_id}/retry-failed")
def retry_failed(record_id: int, _user=CurrentUser):
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
