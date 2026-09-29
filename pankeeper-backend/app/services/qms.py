"""QMS 客户端：转存完成后触发刮削 / STRM 同步。

QMS 侧有自己的详细日志与队列，这里只负责触发并记录结果快照（不轮询等它跑完，
轮询语义留给后续需要「记录页显示 QMS 执行进度」时再加）。
鉴权：X-API-Key（设置页配置）；接口事实见 docs/research/bdsavepro.md §4。
"""
from __future__ import annotations

import httpx

from ..services.settings_svc import get_group


def _client() -> tuple[httpx.Client, str] | None:
    cfg = get_group("settings")["qms"]
    if not cfg.get("enabled") or not cfg.get("url"):
        return None
    headers = {}
    if cfg.get("apikey"):
        headers["X-API-Key"] = cfg["apikey"]
    return httpx.Client(base_url=cfg["url"].rstrip("/"), headers=headers, timeout=15.0), cfg["url"]


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


def test_connection() -> tuple[bool, str]:
    """GET /api/user/info 连接测试（网盘连接页/设置页的「测试」按钮）。"""
    c = _client()
    if c is None:
        return False, "QMS 未启用或未填地址"
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
