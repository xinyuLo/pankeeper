from __future__ import annotations

import re
import time

from . import tmdb

_RECOGNIZE_CACHE: dict[str, tuple[float, dict]] = {}
RECOGNIZE_CACHE_TTL = 30 * 60

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

                    year=real_year or year,
                )
        out.append(it)
    return out

def _search_pool(title: str, year: int | None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    def _pool(t: str) -> list[dict]:
        jobs = [(mt, y) for mt in ("movie", "tv") for y in ([year, None] if year else [None])]

        def _one(mt: str, y: int | None):
            return (tmdb.search(t, y, mt) or {}).get("results") or []

        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            batches = list(ex.map(lambda j: _one(*j), jobs))

        pool: dict[tuple[str, int], dict] = {}
        for (mt, _y), results in zip(jobs, batches):
            for r in results:
                key = (mt, int(r.get("id") or 0))
                if key[1] and key not in pool:
                    pool[key] = {**r, "_mt": mt}
        return list(pool.values())

    pool = _pool(title)
    if not pool:

        m = re.match(r"^[A-Za-z0-9.\- ]+(.+)$", title) or re.match(r"^(.+?)[A-Za-z0-9.\- ]+$", title)
        if m and m.group(1).strip() and m.group(1).strip() != title:
            pool = _pool(m.group(1).strip())
    return pool

def _file_signals(file_names: list[str]) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    for n in (file_names or [])[:12]:
        t, y, _ep = clean_work(n)
        t = (t or "").strip().lower()
        if t or y:
            out.append((t, y))
    return out

def _score_candidate(r: dict, title: str, year: int | None, signals: list[tuple[str, int | None]]) -> tuple[float, int | None]:
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
            score += 80
        if any(sy and yr == sy for _st, sy in signals):
            score += 30
    score += min(float(r.get("popularity") or 0), 10.0)
    return score, (yr or None)

def recognize_candidates(name: str, hint: str = "", file_names: list[str] | None = None) -> dict:
    cache_key = f"{name}|{hint}|{hash(tuple(file_names or []))}"
    now = time.time()
    cached = _RECOGNIZE_CACHE.get(cache_key)
    if cached and now - cached[0] < RECOGNIZE_CACHE_TTL:
        return cached[1]

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

    top_score = scored[0][0]
    scored = [x for x in scored if x[0] >= top_score - 30] or scored[:1]
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
        for s, yr, r in scored[:4]
    ]
    top = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 0.0

    confident = len(scored) == 1 or (top - second >= 40 and top >= 80)
    res = {"ok": True, "confident": confident, "best": candidates[0], "candidates": candidates, "title": title, "year": year}

    _RECOGNIZE_CACHE[cache_key] = (now, res)
    if len(_RECOGNIZE_CACHE) > 64:
        for k in [k for k, (t, _r) in _RECOGNIZE_CACHE.items() if now - t >= RECOGNIZE_CACHE_TTL]:
            _RECOGNIZE_CACHE.pop(k, None)
    return res
