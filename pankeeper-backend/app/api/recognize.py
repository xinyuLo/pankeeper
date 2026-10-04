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
    """识别资源名 → TMDB。返回首条结果，media_name 可直接回填「文件夹更名」。

    media_name 按 QMS 的命名规则生成「标题 (年份)」——QMS 靠文件夹名提取
    名称+年份查 TMDB（实测其改名目标就是 `飞驰人生2 (2024)` 这个格式），
    缺年份照样可能识别失败（2026-10-04 用户：你这连个年份都没有 QMS 不会失败？）。
    """
    name = (body.name or "").strip()
    if not name:
        return {"ok": False, "message": "缺少资源名"}
    items = media_recognize.recognize_batch([name], hint=body.hint.strip())
    it = (items or [{}])[0]
    if not it.get("tmdb_id"):
        return {"ok": False, "message": "未识别到 TMDB 条目"}
    title = it.get("title") or it.get("media_name") or ""
    year = it.get("year")
    return {
        "ok": True,
        "title": title,
        "media_name": f"{title} ({year})" if year else title,
        "year": year,
        "tmdb_id": it.get("tmdb_id"),
        "doubt": it.get("doubt", False),
    }
