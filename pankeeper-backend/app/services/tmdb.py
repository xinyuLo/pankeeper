"""TMDB 客户端：只为「推送带图」服务（联动 qMediaSync，这里补封面/剧照/简介）。

- v3 api_key 从设置 qms.tmdb_api_key 读（设置页「代理配置」tab）
- 连通模式 qms.tmdb_mode：
  * proxy（默认）：可选代理 qms.tmdb_proxy（NAS/国内直连 api.themoviedb.org 常常不通，
    qMediaSync 自己也有同样的代理选项）；留空直连
  * host：按 qms.tmdb_hosts（[{ip, host}]，同 hosts 文件一行一对）把 api.themoviedb.org
    直连到指定 IP——连接走 IP、TLS SNI 与证书校验仍按真域名（httpx sni_hostname 扩展），
    Cloudflare 优选 IP 场景证书是匹配的；自建反代证书对不上时开 qms.tmdb_skip_tls 跳过校验。
    **同域名多行按行序生效：第一行连不上自动换下一行**（ping 测试会把最快 IP 报告给前端置顶）；
    表里没配该域名的条目时退回普通直连（空表不能把推送弄瞎）
- 中文数据 zh-CN；图片路径统一拼 w500（Server酱推送实测定稿，见推送格式规范）
- 任何失败返回 None：富文本推送拿不到数据时回退纯文字，不阻塞主流程
"""
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
    """hosts 表里 domain 的全部候选 IP（按行序，ip 为空的行跳过，重复 IP 去重）。"""
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
    """按连通模式产出候选 (client, sni, target, desc)，**顺序即失败换行的次序**。

    host 模式 = 同域名每个 IP 一个候选；一个都没配 → 普通直连兜底。
    proxy 模式 = 单候选（支持 http:// 与 socks5://，后者依赖 httpx[socks]）。
    代理串非法时按「无候选」处理，调用方如实报失败，不让配置错误炸成 500。
    返回 [] = 没配 API Key 或配置无效。
    qms_cfg 传「测试按钮正在编辑的草稿值」（缺省读已保存配置）。"""
    cfg = qms_cfg if qms_cfg is not None else get_group("settings")["qms"]
    key = (cfg.get("tmdb_api_key") or "").strip()
    if not key:
        return []
    common: dict = {"params": {"api_key": key, "language": LANGUAGE}, "timeout": timeout}
    if (cfg.get("tmdb_mode") or "proxy") == "host":
        ips = _host_ips(cfg, API_HOST)
        if ips:
            # 连 IP、SNI/证书按真域名：httpx 把 sni_hostname 扩展透传给 httpcore，
            # TLS 握手用它做 server_hostname（check_hostname 时证书也按它校验）
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
    """候选依次试：连接层失败（连不上/超时）换下一个（host 多 IP 换下一行）；
    链路通但响应不对（401/5xx）换 IP 也救不了，直接失败。
    2026-10-09 加**一次** 0.5s 退避重试：代理链路（proxy 模式单候选）瞬态抖动很常见——
    超时/连接被重置/429/5xx 大多是秒级瞬态，原地重试一次能扛掉大半，仍失败再换候选/放弃。"""
    for c, sni, _target, _desc in _build_clients(qms_cfg):
        try:
            for attempt in range(2):
                try:
                    resp = c.get(path, params=params, extensions={"sni_hostname": sni} if sni else None)
                except httpx.TransportError:
                    if attempt:
                        break  # 重试过仍连不上：换下一个候选
                    time.sleep(0.5)
                    continue
                except httpx.HTTPError:
                    return None
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code not in (429, 500, 502, 503, 504) or attempt:
                    return None  # 401/404 等换候选也没用；重试过仍瞬态错误 → 放弃
                time.sleep(0.5)
        finally:
            c.close()
    return None


def _short_err(e: Exception) -> str:
    msg = re.sub(r"^\[[^\]]+\]\s*", "", str(e)).rstrip("。")
    return msg[:40] or e.__class__.__name__


def _ping_one(cand: tuple) -> dict:
    """测单个候选：{target, desc, ok, ms?, error?}。连接层失败与 HTTP 状态错都算该候选不通。"""
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
    """测试连通（设置页「代理配置」tab 的测试按钮）：打最轻的鉴权端点 /configuration。

    host 模式同域名多 IP 时**并发测全部候选**（8s 超时），results 逐个报耗时，
    winner=最快的 target（前端据此把该行置顶为生效行）。失败也是 200 + ok=false。"""
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
