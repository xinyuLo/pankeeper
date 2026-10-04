"""TMDB 自识别器：PanKeeper 自己从转存文件名识别媒体信息，不依赖 QMS/LitePan 的刮削记录。

为什么存在：联动后端切到 LitePan 后，刮削状态对外不可见（只有 webhook 进、没有查询出），
Server酱富文本推送需要的 名称/年份/集数/tmdb_id 只能自己识别。识别器在 qms 模式下同样可用
（推送不再依赖 QMS 记录，连"按任务名筛记录会漏"的老坑都一并绕开）。

识别规则（行为模式参考主流刮削器的通用做法，实现为 PanKeeper 原创代码）：
  文件名清洗 → 标题/年份/集数 → TMDB 搜索（年份完全相等优先 → 放开年份 → ±1 相邻年唯一
  命中带 doubt 标记 → 多候选取首个带 doubt）。
"""
from __future__ import annotations

import re

from . import tmdb

# 发布组/编码规格噪音（识别时整段剥掉，别让 "2160p WEB-DL DoVi" 混进标题）
_TAG_NOISE = re.compile(
    r"(2160p|1080p|720p|4k|hdr(?:10\+)?)|dovi|dolby.?vision|web-?dl|webrip|bluray|blu-ray|bdrip|"
    r"h\.?26[45]|x26[45]|hevc|avc|10bit|8bit|60fps|atmos|truehd(?:[ .]?7\.1)?|dd[p+]?[ .]?[57]\.1|"
    r"aac|flac|hq|remux|proper|repack|nf|amzn|dsnp|ctrlhd|web",
    re.I,
)
_EP_SE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")
_EP_CN = re.compile(r"第(\d{1,4})[集话話]")
_EP_E = re.compile(r"(?:^|[- .\[])[Ee](\d{1,3})(?:[- .\]]|$)")
_YEAR = re.compile(r"(19\d{2}|20\d{2})")
_EXT = re.compile(r"\.[a-z0-9]{2,4}$", re.I)


def clean_work(name: str) -> tuple[str, int | None, int | None]:
    """文件名 → (标题, 年份, 集数)。标题可能为空（文件名裸奔时靠调用方兜任务名）。"""
    stem = _EXT.sub("", name.strip())
    ep: int | None = None
    m = _EP_SE.search(stem)
    if not m:
        m2 = _EP_CN.search(stem)
        if m2:
            ep = int(m2.group(1))
            stem = stem[: m2.start()]
        else:
            m3 = _EP_E.search(stem)
            if m3:
                ep = int(m3.group(1))
                stem = stem[: m3.start()]
    else:
        ep = int(m.group(2))
        stem = stem[: m.start()]

    year: int | None = None
    for ym in _YEAR.finditer(stem):
        year = int(ym.group(1))
    if year:
        idx = stem.rfind(str(year))
        stem = stem[:idx]

    stem = _TAG_NOISE.sub(" ", stem)
    title = re.sub(r"[.\-_]+", " ", stem).strip(" -_.[]()·")
    return title, year, ep


def _match(title: str, year: int | None, media_type: str) -> dict | None:
    """TMDB 匹配单条：年份完全相等优先 → 放开年份 → ±1 相邻唯一（doubt）→ 多候选取首（doubt）。"""
    raw = tmdb.search(title, year, media_type)
    results = (raw or {}).get("results") or []
    if not results and year:
        raw = tmdb.search(title, None, media_type)
        results = (raw or {}).get("results") or []
    if not results:
        return None
    date_key = "release_date" if media_type == "movie" else "first_air_date"

    def _yr(r: dict) -> int:
        try:
            return int(str(r.get(date_key) or "")[:4])
        except ValueError:
            return 0

    if year:
        exact = [r for r in results if _yr(r) == year]
        if exact:
            return {**exact[0], "doubt": False}
        adjacent = [r for r in results if abs(_yr(r) - year) == 1]
        if len(adjacent) == 1:
            return {**adjacent[0], "doubt": True}
    best = max(results, key=lambda r: r.get("popularity") or 0)
    return {**best, "doubt": len(results) > 1 or (year is not None and _yr(best) != year)}


def recognize_batch(names: list[str], hint: str = "") -> list[dict]:
    """批量识别。hint = 任务名/分享名（文件名裸奔时的标题兜底）。

    返回 [{name, title, year, episode, media_type, tmdb_id, media_name, doubt}]，
    tmdb_id=None = 没识别出来（推送走纯文字兜底）。"""
    out: list[dict] = []
    for n in names or []:
        title, year, ep = clean_work(n)
        if not title and hint:
            title, year2, _ = clean_work(hint)
            title = title or hint.strip()
            year = year or year2
        media_type = "tv" if ep else "movie"
        it = {
            "name": n, "title": title, "year": year, "episode": ep,
            "media_type": media_type, "tmdb_id": None, "media_name": None, "doubt": False,
        }
        if title:
            hit = _match(title, year, "movie")
            if hit is None and ep:
                hit = _match(title, year, "tv")
            if hit:
                date_key = "release_date" if media_type == "movie" else "first_air_date"
                real_year = int(str(hit.get(date_key) or "")[:4] or 0) or None
                it.update(
                    tmdb_id=hit.get("id"),
                    media_name=hit.get("title") or hit.get("name"),
                    doubt=bool(hit.get("doubt")),
                    # 年份以 TMDB 为准：±1 容错命中时输入年份可能是错的（如文件夹写成
                    # 2025 的飞驰人生2），回填文件夹名必须带真实年份，否则 QMS 照样翻车
                    year=real_year or year,
                )
        out.append(it)
    return out
