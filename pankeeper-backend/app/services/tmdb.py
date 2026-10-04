"""TMDB 客户端：只为「推送带图」服务（联动 qMediaSync，这里补封面/剧照/简介）。

- v3 api_key 从设置 qms.tmdb_api_key 读（设置页 QMS 联动 tab 配置）
- 可选代理 qms.tmdb_proxy：NAS/国内直连 api.themoviedb.org 常常不通（qMediaSync
  自己也有同样的代理选项）；留空直连
- 中文数据 zh-CN；图片路径统一拼 w500（Server酱推送实测定稿，见推送格式规范）
- 任何失败返回 None：富文本推送拿不到数据时回退纯文字，不阻塞主流程
"""
from __future__ import annotations

import httpx

from .settings_svc import get_group

BASE = "https://api.themoviedb.org/3"
IMG_W500 = "https://image.tmdb.org/t/p/w500"
LANGUAGE = "zh-CN"


def img_url(path: str | None) -> str | None:
    """poster_path/still_path/backdrop_path → 完整 w500 URL；空路径返回 None。

    注意：图片 URL 由 Server酱客户端（手机端）拉取，通常手机网络直连
    image.tmdb.org 是通的（实测记录见推送格式规范 §4），这里只拼不下载。"""
    if not path:
        return None
    return f"{IMG_W500}{path}"


def _client() -> httpx.Client | None:
    key = (get_group("settings")["qms"].get("tmdb_api_key") or "").strip()
    if not key:
        return None
    proxy = (get_group("settings")["qms"].get("tmdb_proxy") or "").strip() or None
    return httpx.Client(base_url=BASE, params={"api_key": key, "language": LANGUAGE}, proxy=proxy, timeout=15.0)


def _get(path: str, params: dict | None = None) -> dict | None:
    c = _client()
    if c is None:
        return None
    try:
        resp = c.get(path, params=params)
        if resp.status_code != 200:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None
    finally:
        c.close()


def tv_detail(tmdb_id: int) -> dict | None:
    """剧集详情：name / backdrop_path / overview。"""
    return _get(f"/tv/{tmdb_id}")


def tv_season(tmdb_id: int, season: int) -> list[dict] | None:
    """某季单集清单：[{episode_number, name, overview, still_path}]。"""
    data = _get(f"/tv/{tmdb_id}/season/{season}")
    if data is None:
        return None
    return data.get("episodes") or []


def movie_detail(tmdb_id: int) -> dict | None:
    """电影详情：title / backdrop_path / overview。"""
    return _get(f"/movie/{tmdb_id}")


def search(title: str, year: int | None = None, media_type: str = "movie") -> dict | None:
    """TMDB 搜索：返回原始响应（results 列表），无结果/异常返回 None。

    media_type: movie（year 参数）| tv（first_air_date_year 参数）。"""
    path = "/search/movie" if media_type == "movie" else "/search/tv"
    params: dict = {"query": title}
    if year:
        params["year" if media_type == "movie" else "first_air_date_year"] = year
    return _get(path, params)
