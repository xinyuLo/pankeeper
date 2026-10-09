from __future__ import annotations

import re
import time

import httpx

from .settings_svc import get_group

SC_TAG = "PanKeeper"

def push(title: str, content: str, kind: str = "info", short: str | None = None) -> None:
    cfg = get_group("settings")["notify"]
    if not cfg.get("enabled"):
        return
    gate = {
        "search_done": "on_search", "search_fail": "on_search",
        "auto_done": "on_auto", "auto_fail": "on_auto",
        "cred": "on_cred", "info": None,
    }
    flag = gate.get(kind)
    if flag and not cfg.get(flag, True):
        return

    errors: list[str] = []
    if cfg.get("sendkey"):
        ok, err = _serverchan(cfg["sendkey"], f"{title}", content, short)
        if not ok:
            errors.append(f"Server酱：{err}")
    if cfg.get("webhook"):
        ok, err = _webhook(cfg["webhook"], title, content)
        if not ok:
            errors.append(f"Webhook：{err}")
    _log_push(title, kind, "fail" if errors else "success", "；".join(errors), content)

def _log_push(title: str, kind: str, status: str, error: str, content: str = "") -> None:
    try:
        from ..db import SessionLocal
        from ..models import PushLog

        body = content or ""
        if len(body) > 50000:
            body = body[:50000] + "\n…（超长截断）"
        with SessionLocal() as db:
            db.add(PushLog(ts=time.strftime("%Y-%m-%d %H:%M:%S"), title=title, kind=kind, status=status, error=error, content=body))
            db.commit()
    except Exception:
        pass

def drive_enabled(drive: str) -> bool:
    return bool(get_group("drive_notify").get(drive, True))

def _sc_request(sendkey: str, data: dict) -> httpx.Response:
    m = re.match(r"^sctp(\d+)t", sendkey)
    if m:
        url = f"https://{m.group(1)}.push.ft07.com/send/{sendkey}.send"
        data = {**data, "tags": SC_TAG}
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
    return httpx.post(url, data=data, timeout=10)

def _serverchan(sendkey: str, title: str, content: str, short: str | None = None) -> tuple[bool, str]:
    try:
        data = {"title": title, "desp": content}
        if short:
            data["short"] = short
        resp = _sc_request(sendkey, data)
    except httpx.HTTPError as e:
        return False, _human_net_error(e)
    if resp.status_code != 200:

        return False, f"HTTP {resp.status_code}{_sc_reason(resp)}"
    try:
        body = resp.json()
    except ValueError:
        return True, ""
    code = body.get("code")
    if code in (0, None):
        return True, ""
    return False, str(body.get("message") or f"code={code}")

def _human_net_error(e: Exception) -> str:
    s = str(e)
    low = s.lower()
    if "handshake" in low and ("timed out" in low or "timeout" in low):
        return "连接超时（Server酱服务响应慢或网络不稳定），稍后重试"
    if "timed out" in low or "timeout" in low:
        return "请求超时（网络不稳定或服务无响应），稍后重试"
    if "getaddrinfo" in low or "name or service not known" in low or "nodename nor servname" in low:
        return "域名解析失败（检查设备的网络/DNS）"
    if "connection refused" in low:
        return "连接被拒绝（服务地址不可达）"
    if "reset" in low:
        return "连接被重置（网络中断或服务端断开），稍后重试"
    if "ssl" in low or "certificate" in low:
        return "SSL/证书异常（网络被劫持或设备时间不对）"
    if "unreachable" in low or "network is down" in low:
        return "网络不可达（检查设备联网状态）"
    return s[:80] or e.__class__.__name__

def _sc_reason(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") or body.get("info")
        if msg:
            return f" · {msg}"
    except ValueError:
        pass
    txt = (resp.text or "").strip()[:80]
    return f" · {txt}" if txt else ""

def _webhook(url: str, title: str, content: str) -> tuple[bool, str]:
    try:
        resp = httpx.post(url, json={"title": title, "content": content}, timeout=10)
    except httpx.HTTPError as e:
        return False, _human_net_error(e)
    if resp.status_code < 200 or resp.status_code >= 300:
        return False, f"HTTP {resp.status_code}"
    return True, ""

def test_sendkey(sendkey: str) -> tuple[bool, str]:
    try:
        resp = _sc_request(sendkey, {"title": "PanKeeper 测试", "desp": "推送链路已打通，PanKeeper 的消息会带这个标签。"})
    except httpx.HTTPError as e:
        return False, _human_net_error(e)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}{_sc_reason(resp)}"

    try:
        body = resp.json()
    except ValueError:
        return True, "HTTP 200"
    code = body.get("code")
    if code in (0, None):
        return True, "发送成功"
    return False, str(body.get("message") or f"code={code}")
