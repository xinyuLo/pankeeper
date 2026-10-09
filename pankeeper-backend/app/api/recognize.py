from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..deps import CurrentUser
from ..services import media_recognize

router = APIRouter(prefix="/api", tags=["recognize"])

class RecognizeBody(BaseModel):
    name: str
    hint: str = ""
    share_type: str = ""
    share_url: str = ""
    share_code: str = ""

def _cached_file_names(share_type: str, url: str, code: str) -> list[str]:
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
