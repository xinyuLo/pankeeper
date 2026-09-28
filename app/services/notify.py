"""推送通知：Server 酱 + 自定义 Webhook。

交互契约：推送只服务自动转存（任务 post_notify）；手动转存结果看日志不推送。
批次汇总制（参考 quark-auto-save 的做法但 M1 简化为即时单条）。
"""
from __future__ import annotations

import httpx

from .settings_svc import get_group


def push(title: str, content: str, kind: str = "info") -> None:
    """kind: info|done|fail|part|cred —— 按推送时机开关过滤。"""
    cfg = get_group("settings")["notify"]
    if not cfg.get("enabled"):
        return
    gate = {"done": "on_done", "fail": "on_fail", "part": "on_part", "cred": "on_cred"}
    flag = gate.get(kind)
    if flag and not cfg.get(flag, True):
        return
    if cfg.get("sendkey"):
        _serverchan(cfg["sendkey"], f"{title}", content)
    if cfg.get("webhook"):
        _webhook(cfg["webhook"], title, content)


def _serverchan(sendkey: str, title: str, content: str) -> None:
    try:
        httpx.post(f"https://sctapi.ftqq.com/{sendkey}.send", data={"title": title, "desp": content}, timeout=10)
    except httpx.HTTPError:
        pass  # 推送失败不阻塞主流程


def _webhook(url: str, title: str, content: str) -> None:
    try:
        httpx.post(url, json={"title": title, "content": content}, timeout=10)
    except httpx.HTTPError:
        pass


def test_sendkey(sendkey: str) -> tuple[bool, str]:
    try:
        resp = httpx.post(f"https://sctapi.ftqq.com/{sendkey}.send", data={"title": "PanKeeper 测试", "desp": "测试消息已发送"}, timeout=10)
        return resp.status_code == 200, f"HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        return False, str(e)
