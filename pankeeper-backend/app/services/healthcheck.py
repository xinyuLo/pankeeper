"""网盘凭据每日探活（M3）：定时验证 Cookie 是否还有效，失效即标记并推送提醒。

设计要点
--------
- **频率必须克制**：默认每日 1 次（可配 hour/minute，可整体关闭）。夸克有频率风控，
  过密调用反而可能加速 Cookie 失效——社区实践口径是「每天 1–2 次」。
- **探活不等于续命**：Cookie 有效期由平台服务端决定，定期调用只是「可能的加成」，
  真正的价值是**及早发现失效**，而不是等真去转存时才炸。
- **排在上午而非凌晨**（默认 10:00）：半夜探出失效也没人看，通知等于白发。
- **今日已有请求的网盘直接跳过**：那次请求本身就验证过凭据有效性了，重复探活既是
  浪费，也多一次风控暴露。手动触发（force=True）时不受此限制。
- 只探「配置过凭据」的账号；成功刷新 `last_check`，失败置 `expired`。
- **只在「由好变坏」时推送**，否则每天推同一条失效提醒会变成骚扰。
"""
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
    """探活配置：{enabled, hour, minute}（缺失项由 DEFAULTS 兜底）。"""
    return get_group("health_cfg")


def _refresh_summary(acc_id: int, drive_type: str, display: str, mark_credential_fail: bool = True) -> None:
    """刷新单账号的容量/会员缓存（每日巡检的一部分，随探活顺路）。

    - 凭据失效（CredentialExpired）等价于探活失败：置 expired + 推送（仅由好变坏）。
    - 其他异常（网盘抖动）静默：不动状态、保留旧缓存。
    """
    from ..api.accounts import _cache_summary  # 延迟导入防环（accounts 属 api 层）
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
    """遍历已配置账号逐个探活。

    force=True（手动触发）时忽略两件事：`enabled` 开关，以及「今日已有请求则跳过」
    —— 手动点就是要一个当下的确定答案，不该被优化掉。
    """
    cfg = get_cfg()
    if not force and not cfg.get("enabled", True):
        return {"checked": 0, "failed": [], "skipped": [], "disabled": True, "reason": "每日探活已关闭"}

    # 今日各网盘已有请求数：>0 说明凭据今天已被真实使用过，有效性顺带就验证了，
    # 没必要再单独探一次（省一次请求 = 少一分风控暴露）。
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
                # 延迟导入：deps 依赖 FastAPI，放模块顶部会形成环
                from ..deps import make_adapter_for

                adapter = make_adapter_for(db, acc.type, acc.id)
                nickname = adapter.verify()
                acc.status = "connected"
                acc.nickname = nickname
                acc.last_check = stamp
                db.commit()
                # 探活成功顺手刷新容量/会员缓存：每天一次的固定开销，换首页/卡片数据新鲜。
                # summary 失败不影响探活结论（凭据刚刚验证过，多半是接口抖动）。
                try:
                    _cache_summary(acc.id, adapter.summary())
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001 —— 探活失败原因五花八门，一律置 expired
                acc.status = "expired"
                acc.last_check = stamp
                db.commit()
                failed.append({"acc_id": acc.id, "type": acc.type, "display": acc.display_name, "error": str(e)})
                # 只在「由好变坏」时推送，且尊重该账号的失效通知开关
                if prev_status != "expired" and notify.drive_enabled(str(acc.id)):
                    notify.push(
                        f"PanKeeper：{acc.display_name} 凭据已失效",
                        f"每日探活失败：{e}\n请到「网盘连接」页重新配置 Cookie。",
                        kind="cred",  # 与队列侧一致：受「凭据通知」时机开关(on_cred)控制
                    )

    if checked or skipped:
        tail = f"，跳过 {len(skipped)} 个（今日已活跃）" if skipped else ""
        print(f"[health] 探活完成 {stamp}：检查 {checked} 个账号，失败 {len(failed)} 个{tail}")

    # 被跳过的（今日已活跃）账号：凭据不用再验，但容量/会员快照仍要每日刷新——
    # 活跃账号恰恰是最常在首页/卡片露脸的，不刷的话数据永远停在最后一次页面访问。
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
    """PanSou 在线状态跟着每日探活一起刷新（结果写 pansou_health 缓存）。

    用 requests 而非 httpx（理由同 pansou 模块注释）；未配置也落一条 ok=False，
    让前端首屏能区分"没配"和"检测中"。"""
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
    except Exception:  # noqa: BLE001
        pass


def _prune_stats() -> None:
    """每日清理超期的请求统计（默认保留半年）。"""
    n = reqstat.prune()
    if n:
        print(f"[stats] 已清理 {n} 条超期请求统计")


def _apply_schedule() -> None:
    """按当前配置重排定时任务（幂等）。"""
    if _scheduler is None:
        return
    cfg = get_cfg()

    from apscheduler.triggers.cron import CronTrigger

    for jid in (JOB_ID, PRUNE_JOB_ID):
        try:
            _scheduler.remove_job(jid)
        except Exception:  # noqa: BLE001 —— 任务不存在时忽略
            pass

    # 统计清理是独立的数据保留策略，不受「探活开关」影响
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
    """应用启动时调用（幂等）。"""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        from apscheduler.schedulers.background import BackgroundScheduler

        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        _scheduler.start()
        _apply_schedule()


def reschedule() -> None:
    """配置变更后重排（供 settings API 调用）。"""
    _apply_schedule()


def next_run_at() -> str | None:
    """下次执行时间（字符串），未排期/已关闭时返回 None。"""
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
