"""推送通知：Server 酱 + 自定义 Webhook。

交互契约：推送只服务自动转存（任务 post_notify）；手动转存结果看日志不推送。
批次汇总制（参考 quark-auto-save 的做法但 M1 简化为即时单条）。
"""
from __future__ import annotations

import re
import time

import httpx

from .settings_svc import get_group

# Server 酱推送的标签：SC3 的客户端按 tag 分组，把 PanKeeper 的消息归到一处好找
SC_TAG = "PanKeeper"


def push(title: str, content: str, kind: str = "info", short: str | None = None) -> None:
    """按推送时机开关过滤。kind 对应设置页「推送通知 → 推送时机」三个开关：

    - search_done / search_fail → on_search（搜索转存）
    - auto_done   / auto_fail   → on_auto（自动转存）
    - cred                      → on_cred（凭据过期告警）
    - info                      → 不受时机开关限制（仅受总开关）
    short：Server酱³ 列表简介（Turbo 不支持该参数，会忽略）。

    每次真正发出（未被开关拦掉）都落一行 push_logs：标题/时间/成败/失败原因，推送历史页的数据源。"""
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
    """推送结果落库（含正文快照，推送历史「详情」用）。日志失败绝不影响主流程（推送本身就是旁路）。"""
    try:
        from ..db import SessionLocal
        from ..models import PushLog

        # 正文快照截断：富文本推送带图片链接时可能几十 KB，SQLite 存得下但没必要
        body = content or ""
        if len(body) > 50000:
            body = body[:50000] + "\n…（超长截断）"
        with SessionLocal() as db:
            db.add(PushLog(ts=time.strftime("%Y-%m-%d %H:%M:%S"), title=title, kind=kind, status=status, error=error, content=body))
            db.commit()
    except Exception:
        pass


def drive_enabled(drive: str) -> bool:
    """该账号是否允许推送「失效通知」（网盘连接页卡片上的开关，默认开）。

    参数为账号 id 字符串（多账号粒度）。只是粒度开关：真正的总闸仍是
    settings.notify.enabled（以及 on_cred 时机开关）。
    """
    return bool(get_group("drive_notify").get(drive, True))


def _sc_request(sendkey: str, data: dict) -> httpx.Response:
    """按 sendkey 形态自动适配 Server 酱版本。

    - **Server酱³**：sendkey 形如 `sctp<uid>t...`，接口在 `<uid>.push.ft07.com`，
      且支持 `tags`（客户端按标签分组）。
    - **Server酱 Turbo**：sendkey 形如 `SCT...`，接口在 `sctapi.ftqq.com`，**不支持 tags**。

    两个产品的 key 互不通用、域名也不同，写死任何一个都会让另一半用户发不出去。
    """
    m = re.match(r"^sctp(\d+)t", sendkey)
    if m:
        url = f"https://{m.group(1)}.push.ft07.com/send/{sendkey}.send"
        data = {**data, "tags": SC_TAG}
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
    return httpx.post(url, data=data, timeout=10)


def _serverchan(sendkey: str, title: str, content: str, short: str | None = None) -> tuple[bool, str]:
    """返回 (是否成功, 失败原因)。判活口径与 test_sendkey 一致：HTTP 200 且 body code∈(0, None)。"""
    try:
        data = {"title": title, "desp": content}
        if short:
            data["short"] = short
        resp = _sc_request(sendkey, data)
    except httpx.HTTPError as e:
        return False, str(e)
    if resp.status_code != 200:
        # 带上 Server酱 原始原因——只写「HTTP 400」等于没说，实测这里能直接看到
        # 「[AUTH]错误的Key」（占位符 sendkey）之类，秒定位（2026-10-04 踩坑）
        return False, f"HTTP {resp.status_code}{_sc_reason(resp)}"
    try:
        body = resp.json()
    except ValueError:
        return True, ""
    code = body.get("code")
    if code in (0, None):
        return True, ""
    return False, str(body.get("message") or f"code={code}")


def _sc_reason(resp: httpx.Response) -> str:
    """从失败响应里抠一句人话（message/info 优先，其次裸文本前 80 字）。"""
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
        return False, str(e)
    if resp.status_code < 200 or resp.status_code >= 300:
        return False, f"HTTP {resp.status_code}"
    return True, ""


def test_sendkey(sendkey: str) -> tuple[bool, str]:
    try:
        resp = _sc_request(sendkey, {"title": "PanKeeper 测试", "desp": "推送链路已打通，PanKeeper 的消息会带这个标签。"})
    except httpx.HTTPError as e:
        return False, str(e)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}{_sc_reason(resp)}"
    # 两个版本都回 {code, message}：HTTP 200 也可能 body 里报错，得看 code
    try:
        body = resp.json()
    except ValueError:
        return True, "HTTP 200"
    code = body.get("code")
    if code in (0, None):
        return True, "发送成功"
    return False, str(body.get("message") or f"code={code}")
