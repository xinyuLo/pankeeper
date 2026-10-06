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

设置组 litepan：webhook_url（含 /api/open/automation/events 的完整地址）、
apikey（Bearer 密钥，加密存储）、event（事件名，须与 LitePan 规则里配的一致）。"""
from __future__ import annotations

import requests

from .settings_svc import get_group

TIMEOUT = 8


def _post(url: str, body: dict, apikey: str) -> dict:
    """POST 一个 webhook 事件并解包响应。返回 {ok, matched, triggered, message}。
    LitePan 的 writeOK 信封形状源码里没带走（writeOK 定义不在手头文件），
    解包按两种常见形状兜：{code, data:{...}} 取 data、裸 {...} 直接用。"""
    headers = {"Authorization": f"Bearer {(apikey or '').strip()}"}
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return {"ok": False, "matched": 0, "triggered": [], "message": f"请求失败：{e}"}
    data: dict = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            data = parsed["data"] if isinstance(parsed.get("data"), dict) else parsed
            # 信封带业务码且非成功 → 按 HTTP 200 也算失败处理
            if parsed.get("code") not in (None, 0, 200) and resp.status_code < 400:
                msg = str(parsed.get("message") or "").strip()
                return {"ok": False, "matched": 0, "triggered": [],
                        "message": f"LitePan 返回错误 {parsed.get('code')}{'：' + msg if msg else ''}"}
    except ValueError:
        pass
    if resp.status_code >= 400:
        return {"ok": False, "matched": 0, "triggered": [],
                "message": f"HTTP {resp.status_code}（地址或 API Key 不对？）"}
    return {"ok": True, "matched": data.get("matched") or 0,
            "triggered": data.get("triggered") or [], "message": ""}


def notify_transfer_done(payload: dict) -> dict:
    """转存完成通知。payload：{drive, task, path, files, share_url, share_code, event?}。
    event：转存配置按目录配的事件名（2026-10-06 定稿：不同目录推不同规则），
    没带就回落设置页的全局事件名。返回 {ok, matched, triggered, message}——
    调用方据此写转存日志，别再无脑报「已推送」。"""
    cfg = get_group("litepan")
    url = (cfg.get("webhook_url") or "").strip()
    event = (payload.get("event") or cfg.get("event") or "").strip() or "transfer.done"
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
    res = _post(url, body, cfg.get("apikey") or "")
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
    res = _post(url, {"event": "pankeeper.test", "source": "pankeeper", "path": "/"}, apikey)
    if res["ok"]:
        res["message"] = "连通正常（测试事件未命中规则属预期）"
    return res
