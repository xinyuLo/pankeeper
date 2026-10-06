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


# ===== 候选识别（转存弹窗「识别」按钮的歧义解法，2026-10-06 用户拍板） =====
# 痛点：分享名本身歧义（搜"狂飙"既可能是 2023 剧集也可能是 F1：狂飙飞车电影），
# 旧逻辑按热度自作主张挑一个，用户想存的永远是另一个。解法：movie+tv 双搜出候选池，
# 文件名信号（分享内文件常带英文原名/年份）辅助打分，置信度不够就把候选交给用户挑。


def _search_pool(title: str, year: int | None) -> list[dict]:
    """movie+tv 双搜合并候选池。带年份搜太窄（<3 条）就放开年份再搜一轮补池；
    全空再试**粘连名变体**（"F1狂飙赛车"→"狂飙赛车"：TMDB 对无分隔的混排名常常查不到）。"""
    def _pool(t: str) -> list[dict]:
        pool: dict[tuple[str, int], dict] = {}
        for mt in ("movie", "tv"):
            for y in ([year, None] if year else [None]):
                raw = tmdb.search(t, y, mt) or {}
                for r in raw.get("results") or []:
                    key = (mt, int(r.get("id") or 0))
                    if key[1] and key not in pool:
                        pool[key] = {**r, "_mt": mt}
                if len(pool) >= 10:
                    break
        return list(pool.values())

    pool = _pool(title)
    if not pool:
        # 粘连变体：剥掉首/尾的连续英文数字段再搜（"F1狂飙赛车"→"狂飙赛车"）
        m = re.match(r"^[A-Za-z0-9.\- ]+(.+)$", title) or re.match(r"^(.+?)[A-Za-z0-9.\- ]+$", title)
        if m and m.group(1).strip() and m.group(1).strip() != title:
            pool = _pool(m.group(1).strip())
    return pool


def _file_signals(file_names: list[str]) -> list[tuple[str, int | None]]:
    """分享内文件名 → (清洗后的标题小写, 年份)。英文原名/年份是强指向信号。"""
    out: list[tuple[str, int | None]] = []
    for n in (file_names or [])[:12]:
        t, y, _ep = clean_work(n)
        t = (t or "").strip().lower()
        if t or y:
            out.append((t, y))
    return out


def _score_candidate(r: dict, title: str, year: int | None, signals: list[tuple[str, int | None]]) -> tuple[float, int | None]:
    """单条候选打分：年份/名字贴合为基础，文件名信号（英文原名命中）是大头，热度只做 tie-break。"""
    cn = (r.get("title") or r.get("name") or "").strip().lower()
    orig = (r.get("original_title") or r.get("original_name") or "").strip().lower()
    date_key = "release_date" if r.get("_mt") == "movie" else "first_air_date"
    try:
        yr = int(str(r.get(date_key) or "")[:4])
    except ValueError:
        yr = 0
    score = 0.0
    if year and yr == year:
        score += 60
    t = (title or "").strip().lower()
    if t and cn == t:
        score += 50
    elif t and (t in cn or cn in t):
        score += 25
    if signals:
        if any(st and (st in orig or orig in st or st in cn) for st, _sy in signals):
            score += 80  # 分享内文件名的英文原名/别名命中：强指向
        if any(sy and yr == sy for _st, sy in signals):
            score += 30  # 文件名里的年份与候选一致：辅助信号
    score += min(float(r.get("popularity") or 0), 10.0)
    return score, (yr or None)


def recognize_candidates(name: str, hint: str = "", file_names: list[str] | None = None) -> dict:
    """候选式识别：返回排序后的候选列表 + 是否高置信（前端据此决定直接回填还是弹候选卡片）。

    file_names：分享内文件名（只读预热缓存，没有不影响）——用于消歧信号。
    返回 {ok, confident, best, candidates, title, year, message?}，
    best/candidate 结构：{tmdb_id, media_type, title, original_title, year, poster, overview}。
    """
    title, year, _ep = clean_work(name)
    if not title and hint:
        title, y2, _ = clean_work(hint)
        title = title or hint.strip()
        year = year or y2
    title = (title or "").strip()
    if not title:
        return {"ok": False, "message": "缺少可识别的名字", "candidates": []}
    signals = _file_signals(file_names or [])
    pool = _search_pool(title, year)
    if not pool:
        return {"ok": False, "message": "未识别到 TMDB 条目", "candidates": []}
    scored = []
    for r in pool:
        s, yr = _score_candidate(r, title, year, signals)
        scored.append((s, yr, r))
    scored.sort(key=lambda x: (-x[0], -float(x[2].get("popularity") or 0)))
    candidates = [
        {
            "tmdb_id": r.get("id"),
            "media_type": r.get("_mt"),
            "title": r.get("title") or r.get("name") or "",
            "original_title": r.get("original_title") or r.get("original_name") or "",
            "year": yr,
            "poster": tmdb.img_url(r.get("poster_path")),
            "overview": (r.get("overview") or "").strip()[:120],
        }
        for s, yr, r in scored[:6]
    ]
    top = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # 置信：唯一候选直接算稳；否则第一名要比第二名高出一截、且绝对分不低
    # （年份+名字至少占一样）——有歧义就交给用户挑，别赌热度
    confident = len(scored) == 1 or (top - second >= 40 and top >= 80)
    return {"ok": True, "confident": confident, "best": candidates[0], "candidates": candidates, "title": title, "year": year}
