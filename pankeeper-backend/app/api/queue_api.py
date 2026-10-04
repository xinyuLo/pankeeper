"""转存队列：入队 / 状态（轮询 + SSE）/ 配置。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..deps import CurrentUser
from ..queue.engine import get_engine
from ..services.settings_svc import get_group, save_group

router = APIRouter(prefix="/api/queue", tags=["queue"])


class EnqueueBody(BaseModel):
    name: str
    type: str = "quark"
    acc_id: int | None = None  # 用哪个账号转存；空=该类型第一个账号
    path: str = "/"
    files: int = 0
    size: str = "—"
    share_url: str = ""
    share_code: str = ""
    include_subdirs: bool = True
    # 勾选清单：只转存分享内这些相对路径（目录=整棵子树）；空/None=全部
    file_paths: list[str] | None = None
    # 「带壳转存」（快速转存弹窗）：单壳分享把根文件夹整体转过来 + 可选根文件夹更名
    rename: str = ""
    with_shell: bool = False
    # 显式指定 QMS 联动目标（普通转存弹窗下拉；空 = 按目标目录前缀匹配转存配置，旧行为）。
    # STRM 不再单独指定——与 QMS 自动配对（同一条转存配置的 strm_id），刮削成功才生成
    qms_id: int | None = None
    # 明确关闭联动（弹窗开关关掉）：连目录前缀匹配也不做，转存完什么都不触发
    media_off: bool = False
    # 任务来源：search（默认，搜索转存）/ auto（定时调度）；决定推送走哪个开关
    source: str = "search"


@router.post("/tasks")
def enqueue(body: EnqueueBody, _user=CurrentUser):
    pos = get_engine().enqueue(body.model_dump())
    return {"pos": pos}


@router.get("/state")
def queue_state(_user=CurrentUser):
    return get_engine().state_public()


@router.get("/events")
async def queue_events(token: str = ""):
    """SSE：状态变化推送给前端。鉴权走查询参数（EventSource 不支持自定义 header）。"""
    from ..security import parse_token

    if not parse_token(token):
        raise HTTPException(status_code=401, detail="登录凭证不存在或已过期")

    async def gen():
        last = None
        heartbeat = 0
        while True:
            await asyncio.to_thread(get_engine().wait_change, 0.6)
            snap = json.dumps(get_engine().state_public(), ensure_ascii=False)
            if snap != last:
                last = snap
                heartbeat = 0
                yield f"data: {snap}\n\n"
            else:
                heartbeat += 1
                if heartbeat >= 50:  # ~30s 一次心跳：保活防代理断连，前端也据此重置兜底轮询
                    heartbeat = 0
                    # 用具名事件而非 ": ping" 注释行 —— 注释行不会触发前端 onmessage，
                    # 前端就无法据此判断「SSE 还活着」，只能无条件定时轮询兜底。
                    yield "event: ping\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/config")
def get_queue_config(_user=CurrentUser):
    return get_group("queue_cfg")


@router.put("/config")
def put_queue_config(body: dict, _user=CurrentUser):
    cfg = get_group("queue_cfg")
    for k in ("threads", "gap", "qms", "strm"):
        if k in body:
            v = int(body[k])
            if k == "threads":
                v = max(1, min(4, v))
            else:
                v = max(0, min(3600, v))
            cfg[k] = v
    save_group("queue_cfg", cfg)
    return cfg
