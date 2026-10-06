"""LitePan 对接：联动后端切到 litepan 时，转存完成后把消息推给 LitePan，
后续整理（刮削/STRM/Emby 等）由 LitePan 自动化规则自理，PanKeeper 不再跟踪结果。

协议（2026-10-06 定稿，依据 LitePan Go 源码 lp_webhook.go / lp_automation.go / lp_run.go）：
- 不是 WebSocket，是 **HTTP Webhook**：POST <LitePan地址>/api/open/automation/events，
  JSON 体 {event, source, path}——LitePan 只读这三个字段，多给的键会被忽略
  （drive/task/files 等照发，留作扩展与日志排查）。
- 认证：Authorization: Bearer <LitePan API Key>（LitePan「API Key」页建的任务密钥）。
- LitePan 按 event / source / path_prefix **精确匹配**启用中的 webhook 规则，
  命中即入队执行规则动作链（organize → strm → emby refresh…，异步，响应立回）。
- 响应 data 里带 matched / triggered——能知道有没有规则接住，转存日志如实记录。

设置组 litepan：enabled（总闸，关=全部不推）、webhook_url、apikey（加密存储）。
事件名不在这里配——按目录/任务配（转存配置 lp_event / 任务弹窗），**没填就不联动**
（无全局兜底，2026-10-06 用户定稿）。"""
from __future__ import annotations

import requests

from .settings_svc import get_group

TIMEOUT = 8
WEBHOOK_PATH = "/api/open/automation/events"


def _full_webhook_url(url: str) -> str:
    """Webhook 地址补全：用户只填 LitePan 基地址（如 http://192.168.2.77:5545）时
    自动拼上 /api/open/automation/events（2026-10-06 用户实填基地址，别让他记长路径）。"""
    url = (url or "").strip().rstrip("/")
    if url and not url.endswith(WEBHOOK_PATH):
        url += WEBHOOK_PATH
    return url


def _base_origin(url: str) -> str:
    """从 webhook 地址（基地址或完整路径均可）推 LitePan 的 origin，health 检测用。"""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit((url or "").strip())
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        pass
    return (url or "").strip().rstrip("/")


def litepan_health() -> dict:
    """LitePan 在线状态（设置页状态胶囊用）：打 {基地址}/api/health（免认证、轻量）。
    返回 {ok, message}。"""
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
    """POST 一个 webhook 事件并解包响应。返回 {ok, matched, triggered, message}。

    LitePan 真实信封（2026-10-06 对着本机实例实测）：HTTP 200 时
    `{"success":true,"data":{...},"message":""}`；失败时 HTTP 401/404... 且
    `{"success":false,"message":"缺少 Authorization"/"文件不存在",...}`。
    优先认 success 字段，HTTP 状态与 {code,data} 旧形状兜底。"""
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
    # LitePan 信封：success=false 一律失败，message 是人话原因（缺 Authorization / 文件不存在=key 不对 等）
    if parsed.get("success") is False:
        msg = str(parsed.get("message") or "").strip()
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"LitePan 拒绝：{msg or f'HTTP {resp.status_code}'}"}
    if resp.status_code >= 400:
        msg = str(parsed.get("message") or "").strip()
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"HTTP {resp.status_code}{'：' + msg if msg else '（地址或 API Key 不对？）'}"}
    # 旧形状兜底：信封带业务码且非成功
    if parsed.get("code") not in (None, 0, 200):
        msg = str(parsed.get("message") or "").strip()
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"LitePan 返回错误 {parsed.get('code')}{'：' + msg if msg else ''}"}
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    return {"ok": True, "matched": data.get("matched") or 0,
            "triggered": data.get("triggered") or [], "message": ""}


def notify_transfer_done(payload: dict) -> dict:
    """转存完成通知。payload：{drive, task, path, files, share_url, share_code, event?}。
    event：转存配置按目录配的事件名（2026-10-06 定稿：不同目录推不同规则），
    没带就回落设置页的全局事件名。返回 {ok, matched, triggered, message}——
    调用方据此写转存日志，别再无脑报「已推送」。"""
    cfg = get_group("litepan")
    url = (cfg.get("webhook_url") or "").strip()
    # 事件名来源只有调用方（任务级 > 目录级，resolve_litepan_link 已解析好并保证非空）；
    # 没有全局兜底——没填事件名就不联动（用户 2026-10-06 定稿），这里是防御
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
        "source": "pankeeper",
        "path": payload.get("path", ""),
        "drive": payload.get("drive", ""),
        "task": payload.get("task", ""),
        "files": payload.get("files", []),
        "share_url": payload.get("share_url", ""),
        "share_code": payload.get("share_code", ""),
    }
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
    """设置页「测试」：发一个正常配置不会命中的测试事件，HTTP/信封通了就算 ok
    （matched=0 属预期——真规则的 event/path_prefix 不会配 pankeeper.test）。"""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "message": "还没填写 Webhook 地址"}
    res = _post(_full_webhook_url(url), {"event": "pankeeper.test", "source": "pankeeper", "path": "/"}, apikey)
    if res["ok"]:
        res["message"] = "连通正常（测试事件未命中规则属预期）"
    return res
