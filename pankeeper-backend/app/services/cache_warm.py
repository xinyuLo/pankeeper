"""目录缓存到期自动预热（2026-10-15）：TTL 到点自动调现成的全量预热，单工队列串行跑。

背景：目录缓存只有懒加载 + TTL（cache_cfg.ttl，如 30 小时），过期条目只在有人浏览时才
重拉——没人浏览的网盘到期后「已缓存目录表」就是空的（只有最近用过的夸克有数据，用户实锤）。
前端「自动刷新」开关（cache_cfg.auto）此前只是存了个配置、后端没人消费；本模块让它成真：
每 10 分钟对一次表，now - 上次预热 >= TTL 就把全部已连接账号入队全树预热
（cache_api.enqueue_warm_all：串行、同网盘多账号不并行——防风控，2026-10-07 定稿）。

上次预热时间记在 settings 独立组 cache_warm（前端只整存 cache_cfg 组，互不踩）。
首次没有记录时按现有缓存条目里最新的 cached_at 起算（而不是启动瞬间）——「再活一个 TTL」语义。
"""
from __future__ import annotations

import threading
import time

from .settings_svc import get_group, save_group

WARM_GROUP = "cache_warm"
CHECK_INTERVAL_MIN = 10  # 对表节奏：TTL 以小时计，10 分钟粒度足够

_scheduler = None
_lock = threading.Lock()


def _ttl_seconds() -> int:
    """与 dircache 同口径：总闸关 = 0（缓存都禁了，预热无意义）；单位分钟/小时。"""
    cfg = get_group("cache_cfg")
    if not cfg.get("master", True):
        return 0
    ttl = int(cfg.get("ttl") or 0)
    return ttl * (60 if cfg.get("ttlUnit") == "分钟" else 3600)


def _latest_cached_at() -> int:
    from ..models import DirTreeCacheRow
    from ..db import SessionLocal

    try:
        with SessionLocal() as s:
            v = s.query(DirTreeCacheRow.cached_at).order_by(DirTreeCacheRow.cached_at.desc()).first()
        return int(v[0]) if v and v[0] else 0
    except Exception:  # noqa: BLE001 —— 表还没建等：按没有缓存处理
        return 0


def _read_last(now: float) -> int:
    """上次预热时间；没记录就按现有缓存最新的 cached_at 起算并落库（幂等）。"""
    last = int((get_group(WARM_GROUP) or {}).get("last") or 0)
    if last:
        return last
    seeded = _latest_cached_at() or int(now)
    save_group(WARM_GROUP, {"last": seeded})
    return seeded


def check_and_fire(now: float | None = None, fire=None) -> dict:
    """到期检查：到点就入队全量预热并记时间。fire 可注入（测试）；返回本次判定。"""
    now = time.time() if now is None else now
    cfg = get_group("cache_cfg")
    ttl = _ttl_seconds()
    if ttl <= 0 or not cfg.get("auto", True):
        return {"due": False, "fired": False, "reason": "总闸或自动刷新关闭"}
    last = _read_last(now)
    if now - last < ttl:
        return {"due": False, "fired": False, "reason": "未到期", "next_in_min": int((last + ttl - now) / 60)}
    if fire is None:
        from ..api.cache_api import enqueue_warm_all  # 延迟导入避免环

        count = enqueue_warm_all()
    else:
        count = fire()
    save_group(WARM_GROUP, {"last": int(now)})
    print(f"[cache] 缓存到期（TTL {ttl // 3600} 小时），已自动入队 {count} 个账号的全树预热", flush=True)
    return {"due": True, "fired": True, "accounts": count}


def _job() -> None:
    try:
        check_and_fire()
    except Exception as e:  # noqa: BLE001 —— 定时任务不能把调度线程弄死
        print(f"[cache] 自动预热检查失败：{e}", flush=True)


def start_cache_warm_scheduler() -> None:
    """应用启动时调用（幂等）；启动时先对一次表——数据已过期就立刻补一轮预热。"""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        _scheduler.add_job(_job, IntervalTrigger(minutes=CHECK_INTERVAL_MIN), id="cache_warm", replace_existing=True)
        _scheduler.start()
    print(f"[cache] 目录缓存自动预热已排期：每 {CHECK_INTERVAL_MIN} 分钟对表，按 TTL 到点触发", flush=True)
    _job()


def stop_cache_warm_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
