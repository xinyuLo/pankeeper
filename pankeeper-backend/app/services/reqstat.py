"""网盘请求计数：按「日期 + 网盘」累计 PanKeeper 发出的 HTTP 请求数。

用途
----
- 「网盘日志」页顶部卡片展示今日请求数，并给出橙/红预警。
- 请求过密是 Cookie 被风控失效的主要诱因，所以这个数字值得盯着。

采集方式
--------
挂在各 adapter 的 `httpx.Client(event_hooks={"request": [...]})` 上，**业务代码零侵入**；
计数写库失败一律吞掉（绝不能让统计把正经请求带崩）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..db import SessionLocal
from ..models import RequestStat

# 每日请求量分档阈值（绿 / 橙 / 红）
# 依据：正常自用（每天 1-3 个转存任务 + 每日探活）约 50-150 次；
# 夸克网页端正常浏览一天也在 100-300 次量级；超过 400 已明显不像「人在用」。
WARN_AT = 150
DANGER_AT = 400

_DRIVES = ("baidu", "quark", "115")


def today_key() -> str:
    """今日日期键（YYYY-MM-DD）。"""
    return datetime.now().strftime("%Y-%m-%d")


def bump(drive: str, n: int = 1) -> None:
    """给某网盘当日请求数 +n（幂等建行）。异常一律吞掉，不影响正常请求。"""
    if not drive:
        return
    try:
        key = today_key()
        with SessionLocal() as s:
            row = s.get(RequestStat, (key, drive))
            if row is None:
                s.add(RequestStat(date=key, drive=drive, count=n))
            else:
                row.count = (row.count or 0) + n
            s.commit()
    except Exception as e:  # noqa: BLE001 —— 统计失败不能影响业务
        print(f"[reqstat] 计数失败（已忽略）：{e}")


def hook(drive: str):
    """生成给 httpx `event_hooks` 用的请求回调。"""

    def _on_request(_request) -> None:
        bump(drive)

    return _on_request


def today() -> dict[str, int]:
    """今日各网盘请求数 {drive: count}（未出现的网盘补 0）。"""
    with SessionLocal() as s:
        rows = s.query(RequestStat).filter(RequestStat.date == today_key()).all()
    got = {r.drive: int(r.count or 0) for r in rows}
    return {d: got.get(d, 0) for d in _DRIVES}


def trend(days: int = 7) -> list[dict]:
    """近 N 天（含今日）逐日各网盘请求数，按时间旧→新排列。

    上限 366 天只是防御（API 层已限到 180＝半年保留期），真正的范围由调用方决定。
    """
    days = max(1, min(int(days), 366))
    today_d = datetime.now().date()
    start = (today_d - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    with SessionLocal() as s:
        rows = s.query(RequestStat).filter(RequestStat.date >= start).all()

    bucket: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket.setdefault(r.date, {})[r.drive] = int(r.count or 0)

    out: list[dict] = []
    for i in range(days):
        d = (today_d - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        by = bucket.get(d, {})
        out.append({"date": d, "total": sum(by.values()), "by": by})
    return out


def level(count: int) -> str:
    """请求量档位：ok（绿）| warn（橙）| danger（红）。"""
    if count >= DANGER_AT:
        return "danger"
    if count >= WARN_AT:
        return "warn"
    return "ok"


def retain_days() -> int:
    """统计保留天数（默认 180 天＝半年，可在 settings 的 stats_cfg 调整）。"""
    try:
        from .settings_svc import get_group

        return max(7, int(get_group("stats_cfg").get("retain_days", 180)))
    except Exception:  # noqa: BLE001 —— 配置读不到就用默认值
        return 180


def prune() -> int:
    """删除超期统计行，返回删除条数（供每日定时任务调用）。

    保留期默认半年：够看趋势与回溯，又不至于让 SQLite 无限膨胀。
    失败一律吞掉——清理是维护动作，不能把调度器带崩。
    """
    days = retain_days()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with SessionLocal() as s:
            n = s.query(RequestStat).filter(RequestStat.date < cutoff).delete(synchronize_session=False)
            s.commit()
        return int(n or 0)
    except Exception as e:  # noqa: BLE001
        print(f"[reqstat] 清理失败（已忽略）：{e}")
        return 0
