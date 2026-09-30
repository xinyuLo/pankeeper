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
        "acc_id": t.acc_id,
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
    acc_id: int | None = None  # 用哪个账号跑；空=该类型默认账号
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
    t.acc_id = body.acc_id
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
        _reschedule()
        return _row(t)


@router.put("/tasks/{task_id}")
def update_task(task_id: int, body: PaBody, _user=CurrentUser):
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        _apply(t, body)
        db.commit()
        _reschedule()
        return _row(t)


@router.get("/tasks/{task_id}/share-files")
def task_share_files(task_id: int, _user=CurrentUser):
    """实时解析分享链接内的文件树（「查看」按钮数据源，每次现拉不落缓存）。"""
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..deps import make_adapter_for

    db = SessionLocal()
    try:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not t.share_url:
            raise HTTPException(status_code=400, detail="任务没有分享链接")
        try:
            adapter = make_adapter_for(db, t.type)
        except HTTPException:
            db.close()
            raise
        task_url, task_code = t.share_url, t.share_code

        try:
            files = adapter.list_share(TaskSpec(share_url=task_url, share_code=task_code, include_subdirs=True))
        except ShareBanned as e:
            db.close()
            raise HTTPException(status_code=410, detail=f"分享已失效：{e}")
        except CredentialExpired as e:
            db.close()
            raise HTTPException(status_code=401, detail=str(e))
        except AdapterError as e:
            db.close()
            raise HTTPException(status_code=502, detail=str(e))
    finally:
        pass

    def node(path: str, name: str, is_dir: bool, size: int) -> dict:
        return {"name": name, "is_dir": is_dir, "size": size, "path": path, "kids": []}

    nodes: dict[str, dict] = {}
    root: list[dict] = []
    total = 0
    for f in sorted(files, key=lambda x: (x.path.count("/"), x.path)):
        parent = f.path.rsplit("/", 1)[0] or "/"
        name = f.path.rsplit("/", 1)[-1]
        n = node(f.path, name, f.is_dir, f.size)
        if not f.is_dir:
            total += 1
        nodes[f.path] = n
        pnode = nodes.get(parent)
        (pnode["kids"] if pnode else root).append(n)

    def sort_kids(ns: list[dict]) -> None:
        ns.sort(key=lambda x: (not x["is_dir"], x["name"]))
        for n in ns:
            sort_kids(n["kids"])
    sort_kids(root)
    return {"total": total, "tree": root}


class ParseBody(BaseModel):
    type: str = "baidu"
    share_url: str = ""
    share_code: str = ""


@router.post("/parse-share")
def parse_share(body: ParseBody, _user=CurrentUser):
    """解析分享链接：验证有效性并返回文件数（任务弹窗「解析」按钮数据源）。

    只列分享根一层（include_subdirs=False），控制请求量——这里只是「验一下链接活没活」，
    完整清单是转存执行时的事。
    """
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..deps import make_adapter_for

    if not (body.share_url or "").strip():
        raise HTTPException(status_code=400, detail="请先输入分享链接")
    db = SessionLocal()
    try:
        adapter = make_adapter_for(db, body.type)
        files = adapter.list_share(TaskSpec(share_url=body.share_url.strip(), share_code=body.share_code.strip(), include_subdirs=False))
    except ShareBanned as e:
        raise HTTPException(status_code=410, detail=f"分享已失效：{e}")
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        db.close()
    return {"count": sum(1 for f in files if not f.is_dir), "total": len(files)}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t:
            db.delete(t)
            db.commit()
    _reschedule()
    return {"ok": True}


def _reschedule() -> None:
    from ..services.pa_scheduler import sync_jobs

    sync_jobs()


@router.post("/tasks/{task_id}/run")
def run_task_now(task_id: int, _user=CurrentUser):
    """立即运行一次（忽略 enabled/熔断，但不忽略输入侧去重之外的东西——手动点就是要跑）。"""
    from ..services.pa_scheduler import run_task

    res = run_task(task_id, force=True)
    if not res["queued"]:
        raise HTTPException(status_code=400, detail=res["reason"])
    return res


@router.get("/next-runs")
def next_runs(_user=CurrentUser):
    """各自动任务的下一次 cron 执行时间。"""
    from ..services.pa_scheduler import next_run_times

    return next_run_times()


@router.put("/tasks/{task_id}/enabled")
def toggle_task(task_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        t.enabled = not t.enabled
        db.commit()
        _reschedule()
        return {"enabled": t.enabled}
