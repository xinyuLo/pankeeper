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
    acc_id: int | None = None
    path: str = "/"
    files: int = 0
    size: str = "—"
    share_url: str = ""
    share_code: str = ""
    include_subdirs: bool = True

    file_paths: list[str] | None = None

    rename: str = ""
    with_shell: bool = False

    qms_id: int | None = None

    media_off: bool = False

    lp_event: str = ""

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
                if heartbeat >= 50:
                    heartbeat = 0

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
