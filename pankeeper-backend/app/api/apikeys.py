"""API Key 管理：外部调用凭据的增删改查（系统管理 → API Key 页数据源）。

创建响应里的 key 是完整明文，仅此一次；列表/详情永不回明文与哈希。
本组接口自身要求登录态（CurrentUser）——API Key 也可以调（改密码除外，见 settings）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import CurrentUser
from ..services import apikey_svc

router = APIRouter(prefix="/api/apikeys", tags=["apikeys"])


class CreateBody(BaseModel):
    name: str


class StatusBody(BaseModel):
    is_active: bool


def _item(r) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "key_prefix": r.key_prefix,
        "last_used_at": r.last_used_at,
        "created_at": r.created_at,
        "is_active": bool(r.is_active),
    }


@router.get("")
def list_keys(_user=CurrentUser):
    return {"items": [_item(r) for r in apikey_svc.list_all()]}


@router.post("")
def create_key(body: CreateBody, _user=CurrentUser):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写 Key 名称（标记用途，方便日后管理）")
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Key 名称过长（最多 64 字）")
    row, raw = apikey_svc.create(name)
    # 完整密钥只在这一次响应里出现，前端弹窗展示并提示「仅显示一次」
    return {**_item(row), "key": raw}


@router.put("/{key_id}/status")
def update_status(key_id: int, body: StatusBody, _user=CurrentUser):
    if not apikey_svc.set_status(key_id, body.is_active):
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"ok": True}


@router.delete("/{key_id}")
def delete_key(key_id: int, _user=CurrentUser):
    if not apikey_svc.delete(key_id):
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"ok": True}
