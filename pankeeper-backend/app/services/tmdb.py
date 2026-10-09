from __future__ import annotations

import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .settings_svc import get_group

BASE = "https://api.themoviedb.org/3"
API_HOST = "api.themoviedb.org"
IMG_W500 = "https://image.tmdb.org/t/p/w500"
LANGUAGE = "zh-CN"

def img_url(path: str | None) -> str | None:
    """poster_path/still_path/backdrop_path → 完整 w500 URL；空路径返回 None。

    注意：图片 URL 由 Server酱客户端（手机端）拉取，通常手机网络直连
    image.tmdb.org 是通的，这里只拼不下载，代理/host 映射对它不生效。"""
    if not path:
        return None
    return f"{IMG_W500}{path}"


def _host_ips(cfg: dict, domain: str) -> list[str]:
    hosts = cfg.get("tmdb_hosts")
    if not isinstance(hosts, list):
        return []
    ips: list[str] = []
    for h in hosts:
        if not isinstance(h, dict):
            continue
        if str(h.get("host") or "").strip() != domain:
            continue
        ip = str(h.get("ip") or "").strip()
        if ip and ip not in ips:
            ips.append(ip)
    return ips

def _insecure_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _build_clients(qms_cfg: dict | None = None, timeout: float = 15.0) -> list[tuple]:
    cfg = qms_cfg if qms_cfg is not None else get_group("settings")["qms"]
    key = (cfg.get("tmdb_api_key") or "").strip()
    if not key:
        return []
    common: dict = {"params": {"api_key": key, "language": LANGUAGE}, "timeout": timeout}
    if (cfg.get("tmdb_mode") or "proxy") == "host":
        ips = _host_ips(cfg, API_HOST)
        if ips:

            verify: ssl.SSLContext | bool = _insecure_ctx() if cfg.get("tmdb_skip_tls") else True
            return [
                (
                    httpx.Client(base_url=f"https://{ip}/3", headers={"Host": API_HOST}, verify=verify, **common),
                    API_HOST,
                    ip,
                    f"host 模式直连 {ip}",
                )
                for ip in ips
            ]
        return [(httpx.Client(base_url=BASE, **common), None, "直连", "直连（host 表里没配 api.themoviedb.org）")]
    proxy = (cfg.get("tmdb_proxy") or "").strip() or None
    desc = f"经代理 {proxy}" if proxy else "直连"
    try:
        return [(httpx.Client(base_url=BASE, proxy=proxy, **common), None, desc, desc)]
    except Exception:
        return []

def _get(path: str, params: dict | None = None, qms_cfg: dict | None = None) -> dict | None:
    for c, sni, _target, _desc in _build_clients(qms_cfg):
        try:
            resp = c.get(path, params=params, extensions={"sni_hostname": sni} if sni else None)
            if resp.status_code == 200:
                return resp.json()
            return None
        except httpx.TransportError:
            continue
        except httpx.HTTPError:
            return None
        finally:
            c.close()
    return None

def _short_err(e: Exception) -> str:
    msg = re.sub(r"^\[[^\]]+\]\s*", "", str(e)).rstrip("。")
    return msg[:40] or e.__class__.__name__

def _ping_one(cand: tuple) -> dict:
    c, sni, target, desc = cand
    t0 = time.monotonic()
    try:
        resp = c.get("/configuration", extensions={"sni_hostname": sni} if sni else None)
    except httpx.TransportError as e:
        return {"target": target, "desc": desc, "ok": False, "error": _short_err(e)}
    except httpx.HTTPError as e:
        return {"target": target, "desc": desc, "ok": False, "error": _short_err(e)}
    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code == 200:
        return {"target": target, "desc": desc, "ok": True, "ms": ms}
    if resp.status_code == 401:
        return {"target": target, "desc": desc, "ok": False, "error": "API Key 无效（401）"}
    return {"target": target, "desc": desc, "ok": False, "error": f"HTTP {resp.status_code}"}

def ping(qms_cfg: dict | None = None) -> dict:
    cfg = qms_cfg if qms_cfg is not None else get_group("settings")["qms"]
    cands = _build_clients(cfg, timeout=8.0)
    if not cands:
        saved_key = ((cfg.get("tmdb_api_key") or "").strip())
        return {"ok": False, "message": "还没填 TMDB API Key" if not saved_key else "代理配置无效（检查代理串格式）"}
    with ThreadPoolExecutor(max_workers=len(cands)) as ex:
        results = list(ex.map(_ping_one, cands))

    if len(results) == 1:
        r = results[0]
        if r["ok"]:
            return {"ok": True, "ms": r["ms"], "message": f"TMDB 连通正常（{r['desc']}）", "results": results}
        return {"ok": False, "message": f"连不上 TMDB（{r['desc']}）：{r['error']}", "results": results}

    detail = "，".join(f"{r['target']} {r['ms']}ms" if r["ok"] else f"{r['target']} {r['error']}" for r in results)
    ok_ones = [r for r in results if r["ok"]]
    if ok_ones:
        best = min(ok_ones, key=lambda r: r["ms"])
        return {
            "ok": True,
            "ms": best["ms"],
            "winner": best["target"],
            "message": f"最快 {best['target']}（{best['ms']} ms）；{detail}",
            "results": results,
        }
    return {"ok": False, "message": f"全部候选都连不上：{detail}", "results": results}

def tv_detail(tmdb_id: int) -> dict | None:
    return _get(f"/tv/{tmdb_id}")

def tv_season(tmdb_id: int, season: int) -> list[dict] | None:
    data = _get(f"/tv/{tmdb_id}/season/{season}")
    if data is None:
        return None
    return data.get("episodes") or []

def movie_detail(tmdb_id: int) -> dict | None:
    return _get(f"/movie/{tmdb_id}")

def search(title: str, year: int | None = None, media_type: str = "movie") -> dict | None:
    path = "/search/movie" if media_type == "movie" else "/search/tv"
    params: dict = {"query": title}
    if year:
        params["year" if media_type == "movie" else "first_air_date_year"] = year
    return _get(path, params)
