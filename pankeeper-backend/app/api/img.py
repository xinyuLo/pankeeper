"""TMDB 图片本地代理（GET /img?u=...）。

为什么需要：推送正文里的海报/剧照是 image.tmdb.org 的直链，墙内浏览器拉不到，
推送详情页的图就一片空白。这里由 NAS 代拉一次并落盘缓存，页面只取本机图。

为什么路径是 /img 而不是 /api/img：<img> 标签带不了 Authorization 头，统一鉴权
中间件会把图片请求打成 401。图片是公开素材，不涉隐私；只允许 TMDB 图床域名，
不存在 SSRF 面。

拉取口径与 tmdb 服务一致：设置里 tmdb_mode=proxy 时走 tmdb_proxy，直连失败也回退
一次代理（反过来同理），两边任一能通就成。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ..config import DATA_DIR
from ..services.settings_svc import get_group

router = APIRouter()

ALLOW_HOSTS = {"image.tmdb.org"}
CACHE_DIR = DATA_DIR / "imgcache"
MAX_BYTES = 15 * 1024 * 1024  # 15MB 兜底，防止被塞超大文件
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
EXT_BY_CT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def _cache_path(url: str) -> Path | None:
    """按 URL 哈希找已缓存文件（扩展名不限）。"""
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    for ext in EXTS:
        p = CACHE_DIR / f"{h}{ext}"
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _proxy_map() -> dict[str, str] | None:
    cfg = get_group("settings").get("qms") or {}
    proxy = (cfg.get("tmdb_proxy") or "").strip()
    if (cfg.get("tmdb_mode") or "") == "proxy" and proxy:
        return {"http": proxy, "https": proxy}
    return None


def _fetch(url: str, proxies: dict[str, str] | None) -> tuple[bytes, str]:
    resp = requests.get(url, timeout=20, proxies=proxies, stream=True)
    resp.raise_for_status()
    ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ct.startswith("image/"):
        raise ValueError(f"返回的不是图片（{ct or '无 content-type'}）")
    buf = bytearray()
    for chunk in resp.iter_content(64 * 1024):
        buf.extend(chunk)
        if len(buf) > MAX_BYTES:
            raise ValueError("图片超过 15MB")
    return bytes(buf), ct


@router.get("/img", include_in_schema=False)
def proxy_image(u: str = Query(..., description="TMDB 图片完整 URL")):
    """代拉一张 TMDB 图并缓存；命中缓存直接回文件。"""
    parsed = urlparse(u)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOW_HOSTS:
        raise HTTPException(status_code=400, detail="只允许代理 TMDB 图床（image.tmdb.org）")

    hit = _cache_path(u)
    if hit is not None:
        return FileResponse(hit, headers={"Cache-Control": "public, max-age=86400"})

    proxy = _proxy_map()
    data = ct = None
    last_err = ""
    attempts = [proxy, None] if proxy else [None]
    for px in attempts:  # 先按设置；配了代理就再兜底直连一次
        try:
            data, ct = _fetch(u, px)
            break
        except (requests.RequestException, ValueError) as e:
            last_err = str(e)
    if data is None:
        raise HTTPException(status_code=502, detail=f"图片拉取失败：{last_err or '未知原因'}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / (hashlib.sha1(u.encode("utf-8")).hexdigest() + EXT_BY_CT.get(ct, ".jpg"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass  # 落盘失败不影响本次返回
    return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
