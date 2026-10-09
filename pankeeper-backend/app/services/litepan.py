from __future__ import annotations

import requests

from .settings_svc import get_group

TIMEOUT = 8
WEBHOOK_PATH = "/api/open/automation/events"

def _full_webhook_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not url.endswith(WEBHOOK_PATH):
        url += WEBHOOK_PATH
    return url

def _base_origin(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit((url or "").strip())
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        pass
    return (url or "").strip().rstrip("/")

def litepan_health() -> dict:
    cfg = get_group("litepan")
    origin = _base_origin(cfg.get("webhook_url") or "")
    if not origin:
        return {"ok": False, "message": "未配置地址"}
    try:
        resp = requests.get(f"{origin}/api/health", timeout=5)
        parsed = resp.json() if resp.status_code == 200 else {}
        if parsed.get("success") is True or resp.status_code == 200:
            return {"ok": True, "message": "在线"}
        return {"ok": False, "message": f"HTTP {resp.status_code}"}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "message": str(e)[:80] or "连接失败"}

def _post(url: str, body: dict, apikey: str) -> dict:
    headers = {"Authorization": f"Bearer {(apikey or '').strip()}"}
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return {"ok": False, "matched": 0, "triggered": [], "message": f"请求失败：{e}"}
    parsed: dict = {}
    try:
        body_json = resp.json()
        if isinstance(body_json, dict):
            parsed = body_json
    except ValueError:
        pass

    if parsed.get("success") is False:
        msg = str(parsed.get("message") or "").strip()
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"LitePan 拒绝：{msg or f'HTTP {resp.status_code}'}"}
    if resp.status_code >= 400:
        msg = str(parsed.get("message") or "").strip()
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"HTTP {resp.status_code}{'：' + msg if msg else '（地址或 API Key 不对？）'}"}

    if parsed.get("code") not in (None, 0, 200):
        msg = str(parsed.get("message") or "").strip()
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"LitePan 返回错误 {parsed.get('code')}{'：' + msg if msg else ''}"}
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    return {"ok": True, "matched": data.get("matched") or 0,
            "triggered": data.get("triggered") or [], "message": ""}

def notify_transfer_done(payload: dict) -> dict:
    cfg = get_group("litepan")
    url = (cfg.get("webhook_url") or "").strip()

    event = (payload.get("event") or "").strip()
    if not event:
        msg = "未配置 LitePan 事件名（转存配置目录/任务弹窗里没填），不推送"
        print(f"[litepan] {msg}", flush=True)
        return {"ok": False, "matched": 0, "triggered": [], "message": msg}
    if not url:
        msg = "未配置 LitePan Webhook 地址，跳过推送（设置 → QMS 联动 → LitePan）"
        print(f"[litepan] {msg}", flush=True)
        return {"ok": False, "matched": 0, "triggered": [], "message": msg}
    body = {
        "event": event,
        "path": payload.get("path", ""),
        "drive": payload.get("drive", ""),
        "task": payload.get("task", ""),
        "files": payload.get("files", []),
        "share_url": payload.get("share_url", ""),
        "share_code": payload.get("share_code", ""),
    }

    source = (cfg.get("source") or "").strip()
    if source:
        body["source"] = source
    res = _post(_full_webhook_url(url), body, cfg.get("apikey") or "")
    if not res["ok"]:
        print(f"[litepan] 推送失败：{res['message']}", flush=True)
    elif res["matched"]:
        names = "、".join(str(t.get("name", t.get("id", "?"))) for t in res["triggered"]) or "?"
        res["message"] = f"转存完成已推送 LitePan，命中 {res['matched']} 条自动化规则（{names}），后续整理由 LitePan 处理"
        print(f"[litepan] {res['message']}", flush=True)
    else:
        res["message"] = (f"已推送 LitePan，但未匹配任何自动化规则"
                          f"（检查事件名「{event}」与规则的路径前缀是否配对）")
        print(f"[litepan] {res['message']}", flush=True)
    return res

def test_webhook(url: str, apikey: str) -> dict:
    url = (url or "").strip()
    if not url:
        return {"ok": False, "message": "还没填写 Webhook 地址"}
    body = {"event": "pankeeper.test", "path": "/"}
    source = (get_group("litepan").get("source") or "").strip()
    if source:
        body["source"] = source
    res = _post(_full_webhook_url(url), body, apikey)
    if res["ok"]:
        res["message"] = "连通正常（测试事件未命中规则属预期）"
    return res
