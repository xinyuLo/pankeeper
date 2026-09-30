"""QMS 客户端：转存完成后触发刮削 / STRM 同步。

QMS 侧有自己的详细日志与队列，这里只负责触发并记录结果快照（不轮询等它跑完，
轮询语义留给后续需要「记录页显示 QMS 执行进度」时再加）。
鉴权：X-API-Key（设置页配置）；接口事实见 docs/research/bdsavepro.md §4。
"""
from __future__ import annotations

import httpx

from ..services.settings_svc import get_group


def _client(require_enabled: bool = True, url: str | None = None, apikey: str | None = None) -> tuple[httpx.Client, str] | None:
    """构造 QMS 客户端。
    - require_enabled=False 供「测试连接」使用：测的是地址与 API Key 是否可用，
      不该被「启用联动」开关挡住。
    - url / apikey 传入时优先使用（测试正在编辑、尚未保存的值），否则用库里已保存的。
    """
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
    """POST /api/scrape/pathes/start {"id": N}。返回 (成功?, 消息)。"""
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
    """GET /api/scrape/pathes：QMS 刮削路径列表（转存配置页下拉）。失败返回 None。"""
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
    """GET /api/sync/path-list：QMS STRM 同步路径列表。失败返回 None。"""
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
    """GET /api/scrape/records：查刮削记录（联动推送用——等 QMS 刮完改名再推）。

    返回 list（可能为空）；QMS 未启用/连接失败返回 None（调用方据此放弃本次轮询）。
    记录字段见 qMediaSync controllers/scrape.go：file_name/media_name/tmdb_id/status/
    new_file/season_number/episode_number/type 等。
    """
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


def trigger_strm(strm_id: int) -> tuple[bool, str]:
    """POST /api/sync/path/start {"id": N}（STRM 同步目录挂在 QMS 侧管理）。"""
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


def health() -> dict:
    """QMS 引擎状态（设置页胶囊用，语义同 PanSou 的 /search/health）。

    用「已保存配置」测——反映的是当前联动链路的真实状态，而非输入框草稿。
    """
    cfg = get_group("settings")["qms"]
    if not (cfg.get("url") or "").strip():
        return {"ok": False, "message": "未配置"}
    ok, msg = test_connection()
    return {"ok": ok, "message": msg if not ok else "在线"}


def test_connection(url: str | None = None, apikey: str | None = None) -> tuple[bool, str]:
    """GET /api/user/info 连接测试（网盘连接页/设置页的「测试」按钮）。
    url/apikey 传入时测该值（输入框里正在编辑的值），否则测已保存的。不要求「启用联动」为开。"""
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
