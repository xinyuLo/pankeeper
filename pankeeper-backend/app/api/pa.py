"""自动转存任务 CRUD（执行调度在 M3，这里先把数据接口补齐）。

前端契约：docs/api-contract.md §5；扩展字段（正则/下钻/QMS·STRM 目录）已并进任务表。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser
from ..models import PaTask, RunHistory

router = APIRouter(prefix="/api/pa", tags=["pa"])


def _loads(raw: str, fallback=None):
    """json.loads 防御：脏数据回落默认值。"""
    try:
        return json.loads(raw or "")
    except ValueError:
        return [] if fallback is None else fallback


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
        "exclude_names": [x for x in excl if isinstance(x, str)],
        "exclude_md5s": _loads(t.exclude_md5_json),
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
        "drill_on": bool(t.drill_on),  # 下钻：字段保留、配置不丢；功能短期不实现（2026-10-03 定）
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
def task_share_files(task_id: int, refresh: bool = False, _user=CurrentUser):
    """分享链接内文件树（「查看」/排除清单数据源）。

    走独立的分享清单缓存（share_cache，与目录缓存不同逻辑）：转存每跑完一次刷新一次，
    两次转存之间命中缓存秒开；?refresh=1 忽略缓存直连重拉。"""
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..deps import make_adapter_for
    from ..services.share_cache import build_payload, share_key, share_list_cache

    db = SessionLocal()
    try:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not t.share_url:
            raise HTTPException(status_code=400, detail="任务没有分享链接")
        try:
            adapter = make_adapter_for(db, t.type, t.acc_id)
        except HTTPException:
            db.close()
            raise

        key = share_key(t.type, t.share_url, t.share_code)

        def _live() -> dict:
            files = adapter.list_share(TaskSpec(share_url=t.share_url, share_code=t.share_code, include_subdirs=True))
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
        "total": payload["total"],
        "tree": payload["tree"],
        "files": payload["files"],
        "cached_at": int(cached_at),
        "fresh": pulled,
    }


class ExcludeBody(BaseModel):
    """排除清单回写：文件名 + MD5 双清单（转存时任一命中即排除）。"""

    names: list[str] = []
    md5s: list[str] = []


@router.post("/tasks/{task_id}/exclude")
def task_exclude(task_id: int, body: ExcludeBody, _user=CurrentUser):
    """排除清单确定：回写 exclude_json/exclude_md5_json/exclude_count。"""
    with SessionLocal() as db:
        t = db.get(PaTask, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        names = [n for n in body.names if n]
        md5s = [m for m in body.md5s if m]
        t.exclude_json = json.dumps(names, ensure_ascii=False)
        t.exclude_md5_json = json.dumps(md5s, ensure_ascii=False)
        t.exclude_count = len(names)
        db.commit()
    return {"count": len(names)}


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


@router.get("/tasks/{task_id}/runs")
def task_runs(task_id: int, _user=CurrentUser):
    """转存日志列表：该任务的 RunHistory 卡片（新→旧）。"""
    with SessionLocal() as db:
        rows = (
            db.query(RunHistory)
            .filter(RunHistory.task_id == task_id)
            .order_by(RunHistory.id.desc())
            .limit(50)
            .all()
        )
        out = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "started": r.started,
                    "finished": r.finished,
                    "status": r.status,
                    "add": r.add,
                    "skip": r.skip,
                    "skip_md5": r.skip_md5,
                    "excl": r.excl,
                    "total_share": r.total_share,
                    "regex_miss": r.regex_miss,
                    "message": r.message or "",
                }
            )
    return out


@router.get("/runs/{run_id}")
def run_detail(run_id: int, _user=CurrentUser):
    """转存日志详情：执行信息 + 统计 + 转存/排除文件清单 + 完整日志。"""
    with SessionLocal() as db:
        r = db.get(RunHistory, run_id)
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        t = db.get(PaTask, r.task_id)
        return {
            "id": r.id,
            "task_id": r.task_id,
            "task_name": t.name if t else "",
            "started": r.started,
            "finished": r.finished,
            "status": r.status,
            "message": r.message or "",
            "add": r.add,
            "skip": r.skip,
            "skip_md5": r.skip_md5,
            "fail": r.fail,
            "excl": r.excl,
            "total_share": r.total_share,
            "regex_miss": r.regex_miss,
            "duration": r.duration,
            "save_dir": (t.save_dir if t else "") or "",
            "compare_path": (t.compare_path if t else "") or "",
            "regex_pattern": (t.regex_pattern if t else "") or "",
            "include_subdirs": t.include_subdirs if t else True,
            "transferred": json.loads(r.transferred_json or "[]"),
            "excluded": json.loads(r.excluded_json or "[]"),
            "regex_hit": json.loads(r.regex_hit_json or "[]"),
            "logs": json.loads(r.logs_json or "[]"),
        }
