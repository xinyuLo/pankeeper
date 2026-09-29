"""推送通知：Server 酱 + 自定义 Webhook。

交互契约：推送只服务自动转存（任务 post_notify）；手动转存结果看日志不推送。
批次汇总制（参考 quark-auto-save 的做法但 M1 简化为即时单条）。
"""
from __future__ import annotations

import re

import httpx

from .settings_svc import get_group

# Server 酱推送的标签：SC3 的客户端按 tag 分组，把 PanKeeper 的消息归到一处好找
SC_TAG = "PanKeeper"


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


def _serverchan(sendkey: str, title: str, content: str) -> None:
    try:
        _sc_request(sendkey, {"title": title, "desp": content})
    except httpx.HTTPError:
        pass  # 推送失败不阻塞主流程


def _webhook(url: str, title: str, content: str) -> None:
    try:
        httpx.post(url, json={"title": title, "content": content}, timeout=10)
    except httpx.HTTPError:
        pass


def test_sendkey(sendkey: str) -> tuple[bool, str]:
    try:
        resp = _sc_request(sendkey, {"title": "PanKeeper 测试", "desp": "推送链路已打通，PanKeeper 的消息会带这个标签。"})
    except httpx.HTTPError as e:
        return False, str(e)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    # 两个版本都回 {code, message}：HTTP 200 也可能 body 里报错，得看 code
    try:
        body = resp.json()
    except ValueError:
        return True, "HTTP 200"
    code = body.get("code")
    if code in (0, None):
        return True, "发送成功"
    return False, str(body.get("message") or f"code={code}")
