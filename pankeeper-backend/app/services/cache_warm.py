"""目录缓存到期自动预热（2026-10-15）：TTL 到点自动调现成的全量预热，单工队列串行跑。

背景：目录缓存只有懒加载 + TTL（cache_cfg.ttl，如 30 小时），过期条目只在有人浏览时才
重拉——没人浏览的网盘到期后「已缓存目录表」就是空的。前端「自动刷新」开关（cache_cfg.auto）
由本模块兑现：每 10 分钟对一次表，按账号判断到期（T-009，2026-10-24 14:52 实锤此前全局
一个 last 不分账号、且入队即记账——空转一轮也会把下轮推到 30 小时后）。

T-009 三条铁律：
1. 到期判断**按账号**：每个账号自己的 last + TTL，谁到期预热谁（cache_api.enqueue_warm_all
   带 targets），别的账号不陪跑；
2. **入队≠成功**：last 只在预热终态回调（cache_api 的 on_done）里推进；失败记原因不推进
   ——下次对表（≤10 分钟）自然重试，不静默吞掉一轮；
3. 预热本身 force=True 真打网盘（在 cache_api._warm_walk），不会被自家缓存挡掉。

记账在 settings 独立组 cache_warm（前端只整存 cache_cfg 组，互不踩）：
`{"last": {acc_key: ts}, "errors": {acc_key: 原因}}`；老结构（全局一个 int last）读取时
兼容当作全部账号的兜底值，首次覆写即迁移。
"""
from __future__ import annotations

import threading
import time

from .settings_svc import get_group, save_group

WARM_GROUP = "cache_warm"
CHECK_INTERVAL_MIN = 10  # 对表节奏：TTL 以小时计，10 分钟粒度足够；也是失败后的重试间隔
WARM_TYPES = ("baidu", "quark", "115")

_scheduler = None
_lock = threading.Lock()
_state_lock = threading.Lock()  # cache_warm 组的读改写（回调可能并行完成）


def _ttl_seconds() -> int:
    """与 dircache 同口径：总闸关 = 0（缓存都禁了，预热无意义）；单位分钟/小时。"""
    cfg = get_group("cache_cfg")
    if not cfg.get("master", True):
        return 0
    ttl = int(cfg.get("ttl") or 0)
    return ttl * (60 if cfg.get("ttlUnit") == "分钟" else 3600)


def _accounts() -> list[tuple[str, int, str]]:
    """已连接账号清单 [(type, acc_id, 展示名)]——按账号到期判断与 warm_schedule 共用。"""
    from ..db import SessionLocal
    from ..models import Account

    try:
        with SessionLocal() as s:
            rows = s.query(Account).filter(Account.status == "connected").order_by(Account.id).all()
        return [(r.type, r.id, (r.alias or r.nickname or f"{r.type}#{r.id}")) for r in rows if r.type in WARM_TYPES]
    except Exception:  # noqa: BLE001 —— 表还没建等：按没有账号处理
        return []


def _acc_latest_cached_at(type_: str, acc_id: int) -> int:
    """某账号现有缓存条目里最新的 cached_at（该账号 last 缺失时的起算值）。

    acc 只查 str(acc_id)；**仅当该账号确为该类型默认账号**时才并查 "main"（默认账号
    的缓存键写法，T-009-F3：无脑并查会把默认账号的缓存当成非默认账号自己的，起算值
    被拉新、到期判断偏移）。"""
    from ..db import SessionLocal
    from ..models import DirTreeCacheRow

    accs = [str(acc_id)]
    try:
        default = (get_group("default_accounts") or {}).get(type_)
        if default is not None and int(default) == int(acc_id):
            accs.append("main")
    except Exception:  # noqa: BLE001 —— 没配默认账号：只按 id 查
        pass
    try:
        with SessionLocal() as s:
            v = (
                s.query(DirTreeCacheRow.cached_at)
                .filter(DirTreeCacheRow.type == type_, DirTreeCacheRow.acc.in_(accs))
                .order_by(DirTreeCacheRow.cached_at.desc())
                .first()
            )
        return int(v[0]) if v and v[0] else 0
    except Exception:  # noqa: BLE001
        return 0


def _read_state() -> tuple[dict, dict, dict, dict, int]:
    """cache_warm 组读取 → (last 映射, errors 映射, retry 退避映射, 连败计数, 老结构全局 last)。

    老结构 last 是 int（2026-10-25 前的全局记账）：读取时当兜底值，写入时由
    _fanout_legacy 摊给所有账号后清掉（T-009-F1.1）。"""
    g = get_group(WARM_GROUP) or {}
    last = g.get("last")
    lmap = dict(last) if isinstance(last, dict) else {}
    legacy = int(last) if isinstance(last, (int, float)) else 0
    errors = {str(k): str(v) for k, v in (g.get("errors") or {}).items()}
    retry = {str(k): int(v) for k, v in (g.get("retry") or {}).items() if v}
    fails = {str(k): int(v) for k, v in (g.get("fails") or {}).items() if v}
    return lmap, errors, retry, fails, legacy


def _write_state(lmap: dict, errors: dict, retry: dict, fails: dict) -> None:
    save_group(WARM_GROUP, {"last": lmap, "errors": errors, "retry": retry, "fails": fails})


def _fanout_legacy(lmap: dict) -> None:
    """老结构全局 last 一次性摊给所有已连接账号（就地改 lmap；摊完落库后 legacy 自然消失）。

    T-009-F1.1：不摊的话，首个成功账号的回写会把 dict 写回去、legacy 随之蒸发——
    其余已到期账号丢起算值，下轮被 seed 成 now，白等一整个 TTL（实测两账号同轮 due、
    夸克成功后 baidu 被推后 30 小时）。"""
    # 调用方已持 _state_lock
    legacy = _read_state()[4]
    if not legacy:
        return
    for type_, acc_id, _n in _accounts():
        lmap.setdefault(f"{type_}/{acc_id}", legacy)


def _on_warm_done(acc_key: str, ok: bool, reason: str = "") -> None:
    """预热终态记账（cache_api 完成回调）：**成功才推进**该账号 last；失败记原因 +
    指数退避（10 分钟起、逐次翻倍、封顶 2 小时，T-009-F1.2——失败常是风控中，每
    10 分钟从根全量 force 300 目录只会越打越封）。"""
    now = int(time.time())
    with _state_lock:
        lmap, errors, retry, fails, _legacy = _read_state()
        _fanout_legacy(lmap)
        if ok:
            lmap[acc_key] = now
            errors.pop(acc_key, None)
            retry.pop(acc_key, None)
            fails.pop(acc_key, None)
        else:
            errors[acc_key] = (reason or "未知原因")[:200]
            n = int(fails.get(acc_key) or 0) + 1
            fails[acc_key] = n
            retry[acc_key] = now + min(7200, 600 * 2 ** (n - 1))
        _write_state(lmap, errors, retry, fails)
    if ok:
        print(f"[cache] 预热成功，该账号 last 已推进：{acc_key}", flush=True)
    else:
        wait_min = (retry.get(acc_key, 0) - now) // 60
        print(
            f"[cache] 预热失败：{acc_key}（last 不推进，{wait_min} 分钟后重试）：{(reason or '未知原因')[:120]}",
            flush=True,
        )


def check_and_fire(now: float | None = None, fire=None, accounts: list[tuple[str, int, str]] | None = None) -> dict:
    """到期检查（按账号，T-009）：某账号 last + TTL 到点**且不在失败退避期**才入队该账号。

    fire(due) 可注入（测试），due=[(type, acc_id, acc_key)]，返回入队账号数；默认走
    enqueue_warm_all(targets=due, force=True) 并挂完成回调记账（force=True：到期那轮
    绕缓存真拉 + 过共享限速门；手动预热不带 force，T-009-F1.3）。首见账号（无 last
    无缓存条目）从现在起算一个完整 TTL（「再活一个 TTL」语义），不立即触发。"""
    now = time.time() if now is None else now
    cfg = get_group("cache_cfg")
    ttl = _ttl_seconds()
    if ttl <= 0 or not cfg.get("auto", True):
        return {"due": [], "fired": 0, "reason": "总闸或自动刷新关闭"}
    if accounts is None:
        accounts = _accounts()
    if not accounts:
        return {"due": [], "fired": 0, "reason": "无已连接账号"}
    lmap, _errors, retry, _fails, legacy = _read_state()

    due: list[tuple[str, int, str]] = []
    next_in: list[float] = []
    seeded: dict[int, int] = {}
    for type_, acc_id, _name in accounts:
        key = f"{type_}/{acc_id}"
        last = int(lmap.get(key) or legacy or _acc_latest_cached_at(type_, acc_id) or 0)
        if not last:
            seeded[key] = int(now)
            next_in.append(float(ttl))
            continue
        backoff_until = int(retry.get(key) or 0)
        if now - last >= ttl and now >= backoff_until:
            due.append((type_, acc_id, key))
            next_in.append(max(last + ttl - now, backoff_until - now))
        else:
            # 未到期或失败退避中：取两者里更晚的那个作为"下次可能触发"剩余
            next_in.append(max(last + ttl - now, backoff_until - now))
    if seeded:  # 首见账号的起算值落库一次（幂等）：没有它每次对表都会重新起算、永远不到期
        with _state_lock:
            lmap2, errors2, retry2, fails2, _lg = _read_state()
            _fanout_legacy(lmap2)  # 老结构全局 last 摊给所有账号再落库（T-009-F1.1）
            for k, v in seeded.items():
                lmap2.setdefault(k, v)
            _write_state(lmap2, errors2, retry2, fails2)
    if not due:
        return {"due": [], "fired": 0, "reason": "未到期", "next_in_min": int(min(next_in) / 60) if next_in else 0}
    if fire is None:
        from ..api.cache_api import enqueue_warm_all  # 延迟导入避免环

        def _fire(due_list: list[tuple[str, int, str]]) -> int:
            return enqueue_warm_all(
                targets=[(t, a) for t, a, _k in due_list],
                on_done=lambda t, a, ok, reason: _on_warm_done(f"{t}/{a}", ok, reason),
                force=True,
            )

        fire = _fire
    count = fire(due)
    print(
        f"[cache] 缓存到期（TTL {ttl // 3600} 小时）：入队 {count}/{len(due)} 个账号的全树预热"
        f"（{','.join(k for _, _, k in due)}；成功后才推进各自 last）",
        flush=True,
    )
    return {"due": [k for _, _, k in due], "fired": count}


def warm_schedule(now: float | None = None) -> list[dict]:
    """各账号的自动预热计划（前端「下次刷新」展示，T-009 口径对齐）：

    [{type, acc_id, acc_name, last, next_at, retry_at, error}]。自动刷新关/总闸关时
    next_at=None；失败退避中 next_at = 退避到期时刻（更早的那个触发点）。"""
    now = time.time() if now is None else now
    cfg = get_group("cache_cfg")
    ttl = _ttl_seconds()
    auto_on = ttl > 0 and bool(cfg.get("auto", True))
    lmap, errors, retry, _fails, legacy = _read_state()
    out: list[dict] = []
    for type_, acc_id, acc_name in _accounts():
        key = f"{type_}/{acc_id}"
        last = int(lmap.get(key) or legacy or _acc_latest_cached_at(type_, acc_id) or 0)
        backoff_until = int(retry.get(key) or 0)
        out.append(
            {
                "type": type_,
                "acc_id": acc_id,
                "acc_name": acc_name,
                "last": last,
                "next_at": max(last + ttl, backoff_until) if auto_on and last else None,
                "retry_at": backoff_until,
                "error": errors.get(key, ""),
            }
        )
    return out


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
    print(f"[cache] 目录缓存自动预热已排期：每 {CHECK_INTERVAL_MIN} 分钟对表，按账号 TTL 到点触发（成功才推进 last）", flush=True)
    _job()


def stop_cache_warm_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
