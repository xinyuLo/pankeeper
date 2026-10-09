"""TMDB 识别：给转存弹窗的「文件夹更名」提供一键识别回填。

复用 media_recognize（自识别器，不依赖 QMS/LitePan 刮削记录）。
2026-10-06 起改候选式：识别歧义（同名剧/电影、别名等，实锤：搜"狂飙"想存的是
F1：狂飙飞车电影，识别永远给 2023 剧集）时不再按热度自作主张挑一个，返回候选列表
由用户挑；置信度高时前端直接回填不打扰。
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
    share_type: str = ""  # 分享类型（baidu/quark/115）：配 url+code 用于读清单缓存
    share_url: str = ""  # 分享链接：命中缓存时取文件名做识别消歧信号
    share_code: str = ""  # 提取码


def _cached_file_names(share_type: str, url: str, code: str) -> list[str]:
    """分享内文件名（**只读预热缓存**——识别是旁路，绝不为此打网盘接口碰风控）。
    用户在排除清单/查看文件里看过这个分享，缓存就是热的；没看过就空着，纯按名字识别。"""
    if not (share_type and url):
        return []
    from ..services.share_cache import share_key, share_list_cache

    hit = share_list_cache.get(share_key(share_type, url, code))
    if not hit:
        return []
    payload = hit[0] or {}
    files = payload.get("files") if isinstance(payload, dict) else None
    return [e.get("name") for e in (files or []) if isinstance(e, dict) and e.get("name")][:12]


@router.post("/recognize")
def recognize(body: RecognizeBody, _user=CurrentUser):
    """识别资源名 → TMDB 候选。confident=true 时 media_name 可直接回填「文件夹更名」；
    否则 candidates 按可信度排序（海报/标题/年份/电影或剧集），前端弹卡片让用户挑。

    media_name 按 QMS 的命名规则生成「标题 (年份)」——QMS 靠文件夹名提取
    名称+年份查 TMDB（实测其改名目标就是 `飞驰人生2 (2024)` 这个格式），
    缺年份照样可能识别失败（2026-10-04 用户：你这连个年份都没有 QMS 不会失败？）。
    """
    name = (body.name or "").strip()
    if not name:
        return {"ok": False, "message": "缺少资源名", "candidates": []}
    file_names = _cached_file_names(body.share_type.strip(), body.share_url.strip(), body.share_code.strip())
    res = media_recognize.recognize_candidates(name, hint=body.hint.strip(), file_names=file_names)
    if not res.get("ok"):
        return {"ok": False, "message": res.get("message") or "未识别到 TMDB 条目", "candidates": []}
    best = res.get("best") or {}
    title = best.get("title") or res.get("title") or ""
    year = best.get("year") or res.get("year")
    return {
        "ok": True,
        "confident": bool(res.get("confident")),
        "title": title,
        "media_name": f"{title} ({year})" if year else title,
        "year": year,
        "tmdb_id": best.get("tmdb_id"),
        "doubt": not res.get("confident", False),
        "candidates": res.get("candidates") or [],
        "used_files": bool(file_names),
    }
