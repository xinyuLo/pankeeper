from __future__ import annotations

import requests

from .settings_svc import get_group

TYPE_MAP = {
    "baidu": "baidu",
    "quark": "quark",
    "115": "115",
    "123": "123",
    "aliyun": "ali",
    "xunlei": "xunlei",
    "uc": "uc",
    "magnet": "magnet",
}

class PanSouError(Exception):
    pass

def _base_url() -> tuple[str, int]:
    cfg = get_group("settings")["search"]
    return cfg["pansou_url"].rstrip("/"), int(cfg.get("timeout") or 30)

def search(kw: str, cloud_types: list[str] | None = None, refresh: bool = False) -> list[dict]:
    base, timeout = _base_url()
    if not base:

        raise PanSouError("尚未配置 PanSou 服务地址，请到「系统设置 → 搜索源」填写")
    params = {
        "kw": kw,
        "res": "merge",

        "cloud_types": ",".join(cloud_types or list(TYPE_MAP.keys())),
    }

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

def _magnet_name(url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    from .names import sanitize_name

    try:
        dn = parse_qs(urlsplit(url).query).get("dn", [""])[0]
        return sanitize_name(dn) if dn else ""
    except ValueError:
        return ""

def _map_merged(merged: dict) -> list[dict]:
    from .names import sanitize_name

    out: list[dict] = []
    for pan_type, links in merged.items():
        t = TYPE_MAP.get(pan_type)
        if t is None:
            continue
        for link in links or []:
            dt = (link.get("datetime") or "")[:10]

            url = link.get("url", "")
            name = sanitize_name(link.get("note") or "")
            if not name and t == "magnet":
                name = _magnet_name(url)
            if not name:
                name = url
            out.append(
                {
                    "n": name,
                    "t": t,
                    "s": "—",
                    "d": dt,
                    "ok": True,
                    "hot": False,
                    "url": url,
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
