from __future__ import annotations

import threading
from datetime import datetime

from ..db import SessionLocal
from ..models import Account
from . import notify, reqstat
from .settings_svc import get_group

_scheduler = None
_lock = threading.Lock()
JOB_ID = "pankeeper_daily_health"
PRUNE_JOB_ID = "pankeeper_stats_prune"

def get_cfg() -> dict:
    return get_group("health_cfg")

def _refresh_summary(acc_id: int, drive_type: str, display: str, mark_credential_fail: bool = True) -> None:
    from ..api.accounts import _cache_summary
    from ..deps import make_adapter_for
    from ..adapters.base import CredentialExpired

    stamp = datetime.now().strftime("%m-%d %H:%M")
    try:
        with SessionLocal() as db:
            adapter = make_adapter_for(db, drive_type, acc_id)
            data = adapter.summary()
    except CredentialExpired as e:
        if not mark_credential_fail:
            return
        with SessionLocal() as db:
            acc = db.get(Account, acc_id)
            if acc:
                prev = acc.status
                acc.status = "expired"
                acc.last_check = stamp
                db.commit()
                if prev != "expired" and notify.drive_enabled(str(acc_id)):
                    notify.push(
                        f"PanKeeper：{display} 凭据已失效",
                        f"每日巡检失败：{e}\n请到「网盘连接」页重新配置 Cookie。",
                        kind="cred",
                    )
    except Exception:
        pass
    else:
        _cache_summary(acc_id, data)

def run_check(force: bool = False) -> dict:
    cfg = get_cfg()
    if not force and not cfg.get("enabled", True):
        return {"checked": 0, "failed": [], "skipped": [], "disabled": True, "reason": "每日探活已关闭"}

    active = {} if force else reqstat.today()

    stamp = datetime.now().strftime("%m-%d %H:%M")
    checked, failed, skipped = 0, [], []
    with SessionLocal() as db:
        for acc in db.query(Account).all():
            if not acc.cookies_enc:
                continue
            if active.get(acc.type, 0) > 0:
                skipped.append(acc)
                continue
            checked += 1
            prev_status = acc.status
            try:

                from ..deps import make_adapter_for

                adapter = make_adapter_for(db, acc.type, acc.id)
                nickname = adapter.verify()
                acc.status = "connected"
                acc.nickname = nickname
                acc.last_check = stamp
                db.commit()

                try:
                    _cache_summary(acc.id, adapter.summary())
                except Exception:
                    pass
            except Exception as e:
                acc.status = "expired"
                acc.last_check = stamp
                db.commit()
                failed.append({"acc_id": acc.id, "type": acc.type, "display": acc.display_name, "error": str(e)})

                if prev_status != "expired" and notify.drive_enabled(str(acc.id)):
                    notify.push(
                        f"PanKeeper：{acc.display_name} 凭据已失效",
                        f"每日探活失败：{e}\n请到「网盘连接」页重新配置 Cookie。",
                        kind="cred",
                    )

    if checked or skipped:
        tail = f"，跳过 {len(skipped)} 个（今日已活跃）" if skipped else ""
        print(f"[health] 探活完成 {stamp}：检查 {checked} 个账号，失败 {len(failed)} 个{tail}")

    for acc in skipped:
        _refresh_summary(acc.id, acc.type, acc.display_name)

    _check_pansou()

    return {
        "checked": checked,
        "failed": failed,
        "skipped": [f"{a.type}#{a.id} {a.display_name}" for a in skipped],
        "at": stamp,
    }

def _check_pansou() -> None:
    import requests

    from ..services.settings_svc import get_group, save_group

    url = (get_group("settings")["search"].get("pansou_url") or "").rstrip("/")
    ok, plugins = False, None
    if url:
        try:
            resp = requests.get(f"{url}/api/health", timeout=8)
            data = resp.json()
            ok = True
            plugins = data.get("plugin_count")
        except (requests.RequestException, ValueError) as e:
            print(f"[health] PanSou 探测失败：{e}")
    else:
        print("[health] PanSou 未配置，跳过探测")
    try:
        from ..api.search import _save_health_cache

        _save_health_cache(ok, ok and plugins)
    except Exception:
        pass

def _prune_stats() -> None:
    n = reqstat.prune()
    if n:
        print(f"[stats] 已清理 {n} 条超期请求统计")

def _apply_schedule() -> None:
    if _scheduler is None:
        return
    cfg = get_cfg()

    from apscheduler.triggers.cron import CronTrigger

    for jid in (JOB_ID, PRUNE_JOB_ID):
        try:
            _scheduler.remove_job(jid)
        except Exception:
            pass

    _scheduler.add_job(_prune_stats, CronTrigger(hour=10, minute=30), id=PRUNE_JOB_ID, replace_existing=True)
    print(f"[stats] 请求统计清理已排期：10:30（保留 {reqstat.retain_days()} 天）")

    if not cfg.get("enabled", True):
        print("[health] 每日探活已关闭（请求统计清理照常）")
        return

    hour = int(cfg.get("hour", 10))
    minute = int(cfg.get("minute", 0))
    _scheduler.add_job(run_check, CronTrigger(hour=hour, minute=minute), id=JOB_ID, replace_existing=True)
    print(f"[health] 每日探活已排期：{hour:02d}:{minute:02d}")

def start_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        from apscheduler.schedulers.background import BackgroundScheduler

        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        _scheduler.start()
        _apply_schedule()

def reschedule() -> None:
    _apply_schedule()

def next_run_at() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(JOB_ID)
    if job is not None and job.next_run_time is not None:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M")
    return None

def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
