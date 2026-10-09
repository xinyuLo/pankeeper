from __future__ import annotations

import httpx

from ..services.settings_svc import get_group

def _client(require_enabled: bool = True, url: str | None = None, apikey: str | None = None) -> tuple[httpx.Client, str] | None:
    cfg = get_group("settings")["qms"]
    target = (url or cfg.get("url") or "").strip()
    if not target or (require_enabled and not cfg.get("enabled")):
        return None
    key = apikey if apikey is not None else cfg.get("apikey")
    headers = {}
    if key:
        headers["X-API-Key"] = key
    return httpx.Client(base_url=target.rstrip("/"), headers=headers, timeout=15.0), target

def trigger_scrape(qms_id: int) -> tuple[bool, str]:
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.post("/api/scrape/pathes/start", json={"id": qms_id})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, ""
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()

def scrape_pathes() -> list[dict] | None:
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get("/api/scrape/pathes", params={"page": 1, "pageSize": 200})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, list) else (d or {}).get("list") or []
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()

def sync_pathes() -> list[dict] | None:
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get("/api/sync/path-list", params={"page": 1, "page_size": 200})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, list) else (d or {}).get("list") or []
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()

def scrape_records(name: str | None = None, status: str | None = None, page_size: int = 100) -> list[dict] | None:
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        params: dict = {"page": 1, "pageSize": page_size}
        if name:
            params["name"] = name
        if status:
            params["status"] = status
        resp = client.get("/api/scrape/records", params=params)
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return (data.get("data") or {}).get("list") or []
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()

def clear_scrape_records(ids: list[int]) -> tuple[bool, str]:
    ids = [int(i) for i in (ids or []) if i]
    if not ids:
        return True, ""
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.delete("/api/scrape/records", params={"ids": ",".join(str(i) for i in ids)})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, ""
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()

def scrape_path_status(path_id: int) -> dict | None:
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get(f"/api/scrape/pathes/{path_id}")
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, dict) else None
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()

def trigger_strm(strm_id: int) -> tuple[bool, str]:
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.post("/api/sync/path/start", json={"id": strm_id})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, ""
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()

def get_sync_path(strm_id: int) -> dict | None:
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get(f"/api/sync/path/{strm_id}")
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, dict) else None
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()

def manual_sync(path_id: str, path: str, target_path: str, account_id: int, is_file: bool = False) -> tuple[bool, str]:
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.post(
            "/api/sync/manual",
            json={"path_id": str(path_id), "path": path, "target_path": target_path,
                  "is_file": is_file, "account_id": int(account_id)},
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, str(data.get("message") or "")
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()

def get_emby_config() -> dict | None:
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get("/api/setting/emby-config")
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            if not isinstance(d, dict):
                return None

            return d.get("config") if isinstance(d.get("config"), dict) else d
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()

def refresh_emby_library() -> tuple[bool, str]:
    cfg = get_emby_config()
    if not cfg:
        return False, "拿不到 QMS 的 Emby 配置"
    url = (cfg.get("emby_url") or "").rstrip("/")
    key = cfg.get("emby_api_key") or ""
    if not url or not key:
        return False, "QMS 未配置 Emby 地址/ApiKey"
    if int(cfg.get("enable_refresh_library") or 0) != 1:
        return False, "QMS 未开启「同步完成后刷新媒体库」"
    try:
        resp = httpx.post(f"{url}/Library/Refresh", params={"api_key": key}, timeout=15)
        if resp.status_code in (200, 204):
            return True, ""
        return False, f"Emby 返回 HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        return False, f"Emby 连接失败：{e}"

_STRM_PAIR_CACHE: dict[int, tuple[float, int | None]] = {}
_STRM_PAIR_TTL = 300.0

def strm_id_for_qms(qms_id: int) -> int | None:
    import time as _time

    qid = int(qms_id)
    hit = _STRM_PAIR_CACHE.get(qid)
    if hit and _time.time() - hit[0] < _STRM_PAIR_TTL:
        return hit[1]
    sp = next((x for x in scrape_pathes() or [] if x.get("id") == qid), None)
    dest = (sp.get("dest_path") or "").strip().strip("/") if sp else ""
    sid: int | None = None
    if dest:
        for p in sync_pathes() or []:
            if (p.get("remote_path") or "").strip().strip("/") == dest:
                sid = p.get("id")
                break
    _STRM_PAIR_CACHE[qid] = (_time.time(), sid)
    return sid

def health() -> dict:
    cfg = get_group("settings")["qms"]
    if not (cfg.get("url") or "").strip():
        return {"ok": False, "message": "未配置"}
    ok, msg = test_connection()
    return {"ok": ok, "message": msg if not ok else "在线"}

def test_connection(url: str | None = None, apikey: str | None = None) -> tuple[bool, str]:
    c = _client(require_enabled=False, url=url, apikey=apikey)
    if c is None:
        return False, "请先填写 QMS 地址"
    client, url = c
    try:
        resp = client.get("/api/user/info")
        if resp.status_code == 200 and resp.json().get("code") in (0, 200):
            return True, "QMS 连接正常"
        return False, f"HTTP {resp.status_code}（检查地址或 API Key）"
    except (httpx.HTTPError, ValueError) as e:
        return False, f"连接失败：{e}"
    finally:
        client.close()
