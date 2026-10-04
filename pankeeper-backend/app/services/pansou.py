"""pansou 搜索代理：后端转发（前端不直连，频道配置不暴露给浏览器）。

对接契约见 docs/research/pansou.md：固定 res=merge，前端表格直接吃 merged_by_type；
超时给足 15s+（无缓存首搜 4-10s，命中缓存 <100ms）。

⚠️ 本模块用 requests 而非 httpx：2026-09-28 实测对 NAS 上的 pansou-web 镜像，
httpx 发出的 GET 稳定 502 且请求根本没到容器（nginx 无日志），requests/urllib/curl
同 URL 全部正常。深挖性价比低，先换库（疑似与 pansou-web 内置代理层对 httpx
默认请求头/keep-alive 的处理有关）。
"""
from __future__ import annotations

import requests

from .settings_svc import get_group

# pansou 类型 → 前端 DriveType（没列的丢弃：magnet/ed2k/guangya/tianyi/mobile/pikpak 暂不支持转存）
TYPE_MAP = {
    "baidu": "baidu",
    "quark": "quark",
    "115": "115",
    "123": "123",
    "aliyun": "ali",
    "xunlei": "xunlei",
    "uc": "uc",
}


class PanSouError(Exception):
    pass


def _base_url() -> tuple[str, int]:
    cfg = get_group("settings")["search"]
    return cfg["pansou_url"].rstrip("/"), int(cfg.get("timeout") or 30)


def search(kw: str, cloud_types: list[str] | None = None, refresh: bool = False) -> list[dict]:
    """返回前端 SearchResultItem 形状的列表（含真实转存需要的 url/password）。"""
    base, timeout = _base_url()
    if not base:
        # 前端已有守卫，这里是兜底：直接说人话，别把 requests 的 Invalid URL 漏给用户
        raise PanSouError("尚未配置 PanSou 服务地址，请到「系统设置 → 搜索源」填写")
    params = {
        "kw": kw,
        "res": "merge",
        # 不指定就按我们支持的网盘集合问（省得 pansou 返回一堆转存不了的类型）
        "cloud_types": ",".join(cloud_types or list(TYPE_MAP.keys())),
    }
    # 频道白名单（设置页维护；空 = pansou 的全部频道）
    selected = get_group("settings")["search"].get("channels") or []
    if selected:
        params["channels"] = ",".join(selected)
    if refresh:
        params["refresh"] = "true"
    try:
        resp = requests.get(f"{base}/api/search", params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise PanSouError(f"PanSou 连接失败：{e}") from e
    body = resp.json()
    if body.get("code") != 0:
        raise PanSouError(f"PanSou 返回错误：{body.get('message')}")
    return _map_merged(body.get("data", {}).get("merged_by_type", {}))


def _map_merged(merged: dict) -> list[dict]:
    """merged_by_type → 前端结果行。大小 pansou 不提供，展示 —；时间取日期部分。"""
    from .names import sanitize_name

    out: list[dict] = []
    for pan_type, links in merged.items():
        t = TYPE_MAP.get(pan_type)
        if t is None:
            continue
        for link in links or []:
            dt = (link.get("datetime") or "")[:10]
            # 频道 note 常带 emoji/装饰符：当文件夹名会撞网盘非法字符（errno=2 实锤），
            # 源头洗掉；洗空了回落链接本身（总得有个可认的名字）
            name = sanitize_name(link.get("note") or "") or link.get("url", "")
            out.append(
                {
                    "n": name,
                    "t": t,
                    "s": "—",
                    "d": dt,
                    "ok": True,
                    "hot": False,
                    "url": link.get("url", ""),
                    "share_code": link.get("password") or "",
                    "source": link.get("source", ""),
                }
            )
    return out


def channels() -> list[str]:
    base, _ = _base_url()
    try:
        resp = requests.get(f"{base}/api/health", timeout=8)
        return (resp.json() or {}).get("channels", [])
    except (requests.RequestException, ValueError):
        return []


def health() -> dict:
    base, _ = _base_url()
    try:
        resp = requests.get(f"{base}/api/health", timeout=8)
        data = resp.json()
        return {"ok": True, "ms": int(resp.elapsed.total_seconds() * 1000), "plugins": data.get("plugin_count"), "channels": data.get("channels_count")}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "message": str(e)}
