from __future__ import annotations

from fastapi import APIRouter, Query

from ..deps import CurrentUser
from ..services import reqstat

router = APIRouter(prefix="/api", tags=["drive-logs"])

@router.get("/drive-logs")
def drive_logs(
    days: int = Query(7, ge=1, le=180),
    _user=CurrentUser,
):
    counts = reqstat.today()
    drives = [
        {"drive": d, "count": c, "level": reqstat.level(c)}
        for d, c in counts.items()
    ]
    return {
        "today": {
            "date": reqstat.today_key(),
            "total": sum(counts.values()),
            "drives": drives,
        },
        "trend": reqstat.trend(days),
        "thresholds": {"warn": reqstat.WARN_AT, "danger": reqstat.DANGER_AT},
        "retain_days": reqstat.retain_days(),
    }
