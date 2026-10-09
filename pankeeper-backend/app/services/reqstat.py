from __future__ import annotations

from datetime import datetime, timedelta

from ..db import SessionLocal
from ..models import RequestStat

WARN_AT = 150
DANGER_AT = 400

_DRIVES = ("baidu", "quark", "115")

def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def bump(drive: str, n: int = 1) -> None:
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
    except Exception as e:
        print(f"[reqstat] 计数失败（已忽略）：{e}")

def hook(drive: str):

    def _on_request(_request) -> None:
        bump(drive)

    return _on_request

def today() -> dict[str, int]:
    with SessionLocal() as s:
        rows = s.query(RequestStat).filter(RequestStat.date == today_key()).all()
    got = {r.drive: int(r.count or 0) for r in rows}
    return {d: got.get(d, 0) for d in _DRIVES}

def trend(days: int = 7) -> list[dict]:
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
    if count >= DANGER_AT:
        return "danger"
    if count >= WARN_AT:
        return "warn"
    return "ok"

def retain_days() -> int:
    try:
        from .settings_svc import get_group

        return max(7, int(get_group("stats_cfg").get("retain_days", 180)))
    except Exception:
        return 180

def prune() -> int:
    days = retain_days()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with SessionLocal() as s:
            n = s.query(RequestStat).filter(RequestStat.date < cutoff).delete(synchronize_session=False)
            s.commit()
        return int(n or 0)
    except Exception as e:
        print(f"[reqstat] 清理失败（已忽略）：{e}")
        return 0
