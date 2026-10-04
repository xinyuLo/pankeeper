"""LitePan 对接（**预留**）：联动后端切到 litepan 时，转存完成后把消息推给
LitePan，后续整理（刮削/STRM 等）由 LitePan 自理，PanKeeper 不再跟踪结果。

对接方式：对方用 WebSocket 推送消息——连接方向、消息格式尚未定稿，这里先留好
调用位与消息骨架。settings.litepan.ws_url 配了才真正发，没配只打日志留痕。
定稿后在本文件实现 WS 客户端（连接管理/断线重连/消息序列化），调用方不用动。"""
from __future__ import annotations

import json

from .settings_svc import get_group


def notify_transfer_done(payload: dict) -> None:
    """转存完成通知。payload 结构（骨架，待与 LitePan 定稿）：
    {drive: 网盘类型, task: 任务名, path: 保存目录,
     files: [{name, size}], share_url, share_code}"""
    cfg = get_group("litepan")
    ws_url = (cfg.get("ws_url") or "").strip()
    body = json.dumps(payload, ensure_ascii=False)
    if not ws_url:
        print(f"[litepan] 未配置 ws_url，仅留痕：{body[:200]}", flush=True)
        return
    # TODO(LitePan): WebSocket 连接与推送实现（对接细节定稿后补）
    print(f"[litepan] （预留）应推送转存完成消息到 {ws_url}：{body[:200]}", flush=True)
