"""TMDB 识别：给转存弹窗的「文件夹更名」提供一键识别回填。

复用 media_recognize（自识别器，不依赖 QMS/LitePan 刮削记录）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..deps import CurrentUser
from ..services import media_recognize

router = APIRouter(prefix="/api", tags=["recognize"])


class RecognizeBody(BaseModel):
    name: str  # 资源名/分享名（识别源）
    hint: str = ""  # 任务名兜底（文件名裸奔时当标题用）


@router.post("/recognize")
def recognize(body: RecognizeBody, _user=CurrentUser):
    """识别资源名 → TMDB。返回首条结果，media_name 可直接回填「文件夹更名」。"""
    name = (body.name or "").strip()
    if not name:
        return {"ok": False, "message": "缺少资源名"}
    items = media_recognize.recognize_batch([name], hint=body.hint.strip())
    it = (items or [{}])[0]
    if not it.get("tmdb_id"):
        return {"ok": False, "message": "未识别到 TMDB 条目"}
    return {
        "ok": True,
        "title": it.get("title"),
        "media_name": it.get("media_name"),
        "year": it.get("year"),
        "tmdb_id": it.get("tmdb_id"),
        "doubt": it.get("doubt", False),
    }
