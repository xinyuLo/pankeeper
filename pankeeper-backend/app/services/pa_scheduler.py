"""自动转存任务 cron 调度（M3）。

设计要点
--------
- 每个启用了 cron 的 PaTask 一个 APScheduler job（5 段 cron，`from_crontab` 解析）；
  任务的增删改/启停都会触发 `sync_jobs()` 全量重排（任务量个位数，重排零成本）。
- 触发时只做三件事：校验（enabled/分享链接/是否已被熔断）→ 输入侧去重
  （同一分享链接还有 wait/run 任务在队就不重复入队）→ 入队（source=auto）。
  转存本身完全交给队列引擎，这里不做任何网盘请求——调度器和风控无关。
- last_run/last_status 在**入队时**先置 running，完成/失败由引擎 `_sync_pa_task`
  回写终态；这样「上次执行时间」反映的是调度时刻，与 cron 语义一致。
"""
from __future__ import annotations

import json
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import SessionLocal
from ..models import PaTask

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
JOB_PREFIX = "pa_task_"


def start_pa_scheduler() -> None:
    """应用启动时调用（幂等）。"""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        _scheduler.start()
    sync_jobs()


def sync_jobs() -> dict:
    """按 PaTask 表现状重排所有 cron job（幂等，任务增删改/启停后调用）。"""
    if _scheduler is None:
        return {"scheduled": 0, "reason": "调度器未启动"}
    with SessionLocal() as s:
        tasks = s.query(PaTask).all()
    wanted: dict[str, tuple[int, str]] = {}
    for t in tasks:
        if t.enabled and (t.cron or "").strip():
            wanted[f"{JOB_PREFIX}{t.id}"] = (t.id, t.cron.strip())

    for job in list(_scheduler.get_jobs()):
        if job.id.startswith(JOB_PREFIX) and job.id not in wanted:
            job.remove()

    scheduled, skipped = [], []
    for jid, (tid, cron) in wanted.items():
        try:
            trigger = CronTrigger.from_crontab(cron)
        except ValueError as e:
            skipped.append({"id": tid, "cron": cron, "error": str(e)})
            continue
        _scheduler.add_job(run_task, trigger, args=[tid], id=jid, replace_existing=True,
                           max_instances=1, coalesce=True, misfire_grace_time=300)
        scheduled.append(tid)
    if scheduled or skipped:
        print(f"[pa-sched] 自动任务排期：{len(scheduled)} 个生效" + (f"，{len(skipped)} 个 cron 非法跳过" if skipped else ""))
    return {"scheduled": scheduled, "skipped": skipped}


def run_task(task_id: int, force: bool = False) -> dict:
    """触发一次自动任务（cron 到点或前端「立即运行」）。

    只入队不发网盘请求；返回 {queued, reason}。
    """
    from ..queue.engine import get_engine

    with SessionLocal() as s:
        t = s.get(PaTask, task_id)
        if t is None:
            return {"queued": False, "reason": "任务不存在"}
        if not force:
            if not t.enabled:
                return {"queued": False, "reason": "任务已停用"}
            if not (t.share_url or "").strip():
                return {"queued": False, "reason": "任务缺少分享链接"}
            if t.ban_reason:
                return {"queued": False, "reason": f"分享已失效（{t.ban_reason}），已熔断；更新分享链接后自动恢复"}

        engine = get_engine()
        if not force:
            # 输入侧去重：同一分享链接还有任务在队列里跑/等，就不再排一份
            for q in engine.snapshot()["tasks"]:
                if q.get("status") in ("wait", "run") and q.get("shareUrl") == t.share_url:
                    return {"queued": False, "reason": "同一分享链接已在队列中，跳过本次"}

        try:
            excl = json.loads(t.exclude_json or "[]")
        except ValueError:
            excl = []
        engine.enqueue({
            "name": t.name or "自动任务",
            "type": t.type,
            "path": t.save_dir or "/",
            "share_url": t.share_url,
            "share_code": t.share_code,
            "include_subdirs": t.include_subdirs,
            "source": "auto",
            "acc_id": t.acc_id,
            "pa_task_id": t.id,
            "exclude_names": excl,
            "compare_path": t.compare_path or "",
        })
        t.last_run = time.strftime("%m-%d %H:%M")
        t.last_status = "running"
        s.commit()
        return {"queued": True, "task": t.name}


def next_run_times() -> dict[int, str]:
    """各自动任务的下一次执行时间（设置页展示）。"""
    if _scheduler is None:
        return {}
    out: dict[int, str] = {}
    for job in _scheduler.get_jobs():
        if job.id.startswith(JOB_PREFIX) and job.next_run_time is not None:
            tid = int(job.id[len(JOB_PREFIX):])
            out[tid] = job.next_run_time.strftime("%Y-%m-%d %H:%M")
    return out
