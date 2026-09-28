"""自动转存任务 CRUD（执行调度在 M3，这里先把数据接口补齐）。

前端契约：docs/api-contract.md §5；扩展字段（正则/下钻/QMS·STRM 目录）已并进任务表。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser
from ..models import PaTask

router = APIRouter(prefix="/api/pa", tags=["pa"])


def _row(t: PaTask) -> dict:
    try:
        excl = json.loads(t.exclude_json or "[]")
    except ValueError:
        excl = []
    return {
        "id": t.id,
        "type": t.type,
        "name": t.name,
        "enabled": t.enabled,
        "share_url": t.share_url,
        "share_code": t.share_code,
        "save_dir": t.save_dir,
        "compare_path": t.compare_path or "",
        "include_subdirs": t.include_subdirs,
        "cron": t.cron,
        "exclude_count": t.exclude_count,
        "exclIdx": excl if all(isinstance(x, int) for x in excl) else [],
        "last_run": t.last_run,
        "last_status": t.last_status,
        "last_result": t.last_result,
        "post_qms": t.post_qms,
        "post_notify": t.post_notify,
        # 扩展字段（原内存 map 已并表）
        "qms_id": t.qms_id,
        "strm_id": t.strm_id,
        "regex_pattern": t.regex_pattern or "",
        "regex_replace": t.regex_replace or "",
        "drill_on": bool(t.drill_on),
        "drill": json.loads(t.drill_json or "[]"),
        "ban_reason": t.ban_reason or "",
    }


class PaBody(BaseModel):
    type: str = "baidu"
    name: str
    enabled: bool = True
    share_url: str = ""
    share_code: str = ""
    save_dir: str = ""
    compare_path: str = ""
    include_subdirs: bool = True
    cron: str = ""
    qms_id: int | None = None
    strm_id: int | None = None
    regex_pattern: str = ""
    regex_replace: str = ""
    drill_on: bool = False
    drill: list[str] = []
    post_qms: bool = False
    post_notify: bool = True


def _apply(t: PaTask, body: PaBody) -> None:
    t.type = body.type
    t.name = body.name
    t.enabled = body.enabled
    t.share_url = body.share_url
    t.share_code = body.share_code
    t.save_dir = body.save_dir
    t.compare_path = body.compare_path
    t.include_subdirs = body.include_subdirs
    t.cron = body.cron
    t.qms_id = body.qms_id
    t.strm_id = body.strm_id
    t.regex_pattern = body.regex_pattern
    t.regex_replace = body.regex_replace
    t.drill_on = body.drill_on
    t.drill_json = json.dumps(body.drill, ensure_ascii=False)
    t.post_qms = body.post_qms
    t.post_notify = body.post_notify


@router.get("/tasks")
def list_tasks(type: str = "", _user=CurrentUser):
    with SessionLocal() as db:
        q = db.query(PaTask).order_by(PaTask.type, PaTask.id)
        if type:
            q = q.filter(PaTask.type == type)
        return [_row(t) for t in q.all()]


@router.post("/tasks")
def create_task(body: PaBody, _user=CurrentUser):
    with SessionLocal() as db:
        t = PaTask()
        _apply(t, body)
        db.add(t)
        db.commit()
        return _row(t)


@router.put("/tasks/{task_id}")
def update_task(task_id: int, body: PaBody, _user=CurrentUser):
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        _apply(t, body)
        db.commit()
        return _row(t)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t:
            db.delete(t)
            db.commit()
    return {"ok": True}


@router.put("/tasks/{task_id}/enabled")
def toggle_task(task_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        t.enabled = not t.enabled
        db.commit()
        return {"enabled": t.enabled}
