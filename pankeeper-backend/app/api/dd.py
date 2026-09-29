"""转存配置目录（快速转存依据）+ QMS/STRM 目录字典。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser
from ..models import DdItem, QmsPath, StrmPath

router = APIRouter(prefix="/api", tags=["dd"])


def _row(r: DdItem) -> dict:
    return {
        "id": r.id, "type": r.type, "account": r.account, "sort": r.sort,
        "name": r.name, "path": r.path, "is_default": r.is_default,
        "qms_on": r.qms_on, "qms_id": r.qms_id, "strm_id": r.strm_id,
    }


@router.get("/dd/items")
def list_items(_user=CurrentUser):
    with SessionLocal() as db:
        rows = db.query(DdItem).order_by(DdItem.type, DdItem.sort, DdItem.id).all()
    return [_row(r) for r in rows]


@router.get("/dd/default")
def get_default(type: str, _user=CurrentUser):
    with SessionLocal() as db:
        rows = db.query(DdItem).filter(DdItem.type == type).order_by(DdItem.sort, DdItem.id).all()
    for r in rows:
        if r.is_default:
            return _row(r)
    return rows[0] and _row(rows[0]) if rows else None


@router.get("/dd/has-config")
def has_config(type: str, _user=CurrentUser):
    with SessionLocal() as db:
        return db.query(DdItem).filter(DdItem.type == type).count() > 0


class DdBody(BaseModel):
    id: int | None = None
    type: str
    account: str = "main"
    sort: int = 1
    name: str
    path: str
    qms_on: bool = False
    qms_id: int | None = None
    strm_id: int | None = None


@router.post("/dd/items")
def create_item(body: DdBody, _user=CurrentUser):
    with SessionLocal() as db:
        dup = db.query(DdItem).filter(DdItem.type == body.type, DdItem.account == body.account, DdItem.name == body.name).count()
        if dup:
            raise HTTPException(status_code=400, detail="同账号下已存在同名目录")
        # 每账号唯一默认：该账号第一条自动设默认
        count = db.query(DdItem).filter(DdItem.type == body.type, DdItem.account == body.account).count()
        row = DdItem(**body.model_dump(), is_default=count == 0)
        db.add(row)
        db.commit()
        return _row(row)


@router.put("/dd/items/{item_id}")
def update_item(item_id: int, body: DdBody, _user=CurrentUser):
    with SessionLocal() as db:
        row = db.get(DdItem, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        dup = (
            db.query(DdItem)
            .filter(DdItem.type == body.type, DdItem.account == body.account, DdItem.name == body.name, DdItem.id != item_id)
            .count()
        )
        if dup:
            raise HTTPException(status_code=400, detail="同账号下已存在同名目录")
        # id 是主键：body 里未传时 model_dump 会带 id=None，setattr 会把 rowid 写 NULL
        # （sqlite 报 datatype mismatch）——更新语义下必须跳过。
        for k, v in body.model_dump().items():
            if k == "id":
                continue
            setattr(row, k, v)
        db.commit()
        return _row(row)


@router.delete("/dd/items/{item_id}")
def delete_item(item_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        row = db.get(DdItem, item_id)
        if row:
            db.delete(row)
            db.commit()
    return {"ok": True}


@router.put("/dd/items/{item_id}/default")
def set_default(item_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        row = db.get(DdItem, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        for other in db.query(DdItem).filter(DdItem.type == row.type, DdItem.account == row.account).all():
            other.is_default = other.id == item_id
        db.commit()
    return {"ok": True}


@router.get("/qms/paths")
def qms_paths(_user=CurrentUser):
    with SessionLocal() as db:
        rows = db.query(QmsPath).order_by(QmsPath.id).all()
    return [{"id": r.id, "media_type": r.media_type, "source_path": r.source_path} for r in rows]


@router.get("/strm/paths")
def strm_paths(_user=CurrentUser):
    with SessionLocal() as db:
        rows = db.query(StrmPath).order_by(StrmPath.id).all()
    return [{"id": r.id, "remote_path": r.remote_path} for r in rows]
