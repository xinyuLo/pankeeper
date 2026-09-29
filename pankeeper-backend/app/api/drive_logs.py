"""网盘日志：请求量统计（顶部卡片 + 趋势图）+ 风控预警档位。"""
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
    """今日各网盘请求数（带 ok/warn/danger 档位）+ 近 N 天趋势。

    - `today.drives`：顶部卡片数据源，顺序固定（百度 / 夸克 / 115）
    - `trend`：逐日 `{date, total, by}`，按时间旧→新，供折线图使用
    - `thresholds`：档位阈值原样下发，前端不必写死
    - `retain_days`：统计保留天数（默认 180＝半年），供页面说明区展示
    """
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
