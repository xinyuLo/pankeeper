from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser, make_adapter_for
from ..models import Account, now_str
from ..services.settings_svc import get_group, save_group
from ..services.notify import drive_enabled
from ..security import encrypt_credential

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

META = {
    "baidu": {"short": "百度", "color": "#1677ff", "cred_kind": "Cookie", "note": "适配器开发中"},
    "quark": {"short": "夸克", "color": "#13c2c2", "cred_kind": "Cookie", "note": "已实现：转存/清单/目录/重命名"},
    "115": {"short": "115", "color": "#722ed1", "cred_kind": "Cookie / 扫码", "note": "适配器开发中"},
}
ORDER = ("baidu", "quark", "115")

def _acc_or_404(db, acc_id: int) -> Account:
    acc = db.get(Account, acc_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return acc

def _default_map(db) -> dict:
    from ..services.settings_svc import get_group

    try:
        return get_group("default_accounts") or {}
    except Exception:
        return {}

def _row(acc: Account, default_map: dict | None = None) -> dict:
    meta = META.get(acc.type, {"short": acc.type, "color": "#888", "cred_kind": "Cookie", "note": ""})
    if default_map is None:
        with SessionLocal() as db:
            default_map = _default_map(db)

    explicit = default_map.get(acc.type)
    is_default = (acc.id == explicit) if explicit else _is_first_of_type(acc)
    return {
        "id": acc.id,
        "type": acc.type,
        "alias": acc.alias,
        "display": acc.display_name,
        "short": meta["short"],
        "color": meta["color"],
        "cred_kind": meta["cred_kind"],
        "note": meta["note"],
        "status": acc.status,
        "nickname": acc.nickname,
        "last_check": acc.last_check,
        "is_default": is_default,

        "notify": bool(acc.cookies_enc) and drive_enabled(str(acc.id)),

        "summary": _cached_summary(acc.id),
    }

def _is_first_of_type(acc: Account) -> bool:
    with SessionLocal() as db:
        first = db.query(Account).filter(Account.type == acc.type).order_by(Account.id).first()
    return bool(first and first.id == acc.id)

VALID_DRIVE_TYPES = ("baidu", "quark", "115")

@router.get("/root-dirs")
def get_root_dirs(_user=CurrentUser):
    return get_group("root_cfg")

class RootDirBody(BaseModel):
    type: str
    path: str = ""

@router.put("/root-dirs")
def put_root_dir(body: RootDirBody, _user=CurrentUser):
    if body.type not in VALID_DRIVE_TYPES:
        raise HTTPException(status_code=400, detail=f"未知网盘类型：{body.type}")
    cfg = get_group("root_cfg")
    path = (body.path or "").strip()
    if path:
        cfg[body.type] = path
    else:
        cfg.pop(body.type, None)
    save_group("root_cfg", cfg)
    return cfg

@router.get("")
def list_accounts(_user=CurrentUser):
    out = []
    with SessionLocal() as db:
        dmap = _default_map(db)
        rows = db.query(Account).order_by(Account.type, Account.id).all()
        for acc in rows:
            out.append(_row(acc, dmap))
    return out

class CredentialBody(BaseModel):
    cookies: str
    alias: str = ""

class AliasBody(BaseModel):
    alias: str = ""

def _save_and_verify(acc: Account, cookies: str) -> dict:
    with SessionLocal() as db:
        row = db.get(Account, acc.id)
        row.cookies_enc = encrypt_credential(cookies.strip())
        row.last_check = "从未配置"
        db.commit()
    try:
        with SessionLocal() as db:
            adapter = make_adapter_for(db, acc.type, acc.id)
            nickname = adapter.verify()
        with SessionLocal() as db:
            row = db.get(Account, acc.id)
            row.status = "connected"
            row.nickname = nickname
            row.last_check = now_str()
            db.commit()
        return {"ok": True, "status": "connected", "nickname": nickname}
    except HTTPException:
        raise
    except Exception as e:
        with SessionLocal() as db:
            row = db.get(Account, acc.id)
            if row:
                row.status = "expired"
                row.last_check = now_str()
                db.commit()
        raise HTTPException(status_code=400, detail=f"凭据验证失败：{e}")

@router.post("/{type}")
def add_account(type: str, body: CredentialBody, _user=CurrentUser):
    if type not in ORDER:
        raise HTTPException(status_code=404, detail="未知网盘")
    with SessionLocal() as db:
        acc = Account(type=type, alias=body.alias.strip())
        db.add(acc)
        db.commit()
        acc_id = acc.id
    try:
        return _save_and_verify(acc, body.cookies)
    finally:
        pass

@router.put("/{acc_id}/credential")
def put_credential(acc_id: int, body: CredentialBody, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        if body.alias.strip():
            acc.alias = body.alias.strip()
        db.commit()
    return _save_and_verify(acc, body.cookies)

@router.put("/{acc_id}/alias")
def set_alias(acc_id: int, body: AliasBody, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        acc.alias = body.alias.strip()
        db.commit()
    return {"ok": True, "alias": acc.alias}

@router.delete("/{acc_id}")
def delete_account(acc_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        acc_type = acc.type
        db.delete(acc)
        db.commit()
    from ..services.settings_svc import get_group, save_group

    dmap = get_group("default_accounts") or {}
    if dmap.get(acc_type) == acc_id:
        dmap.pop(acc_type, None)
        save_group("default_accounts", dmap)
    return {"ok": True}

@router.put("/{acc_id}/set-default")
def set_default_account(acc_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        acc_type = acc.type
    from ..services.settings_svc import get_group, save_group

    dmap = get_group("default_accounts") or {}
    if dmap.get(acc_type) == acc_id:
        dmap.pop(acc_type, None)
    else:
        dmap[acc_type] = acc_id
    save_group("default_accounts", dmap)
    return {"ok": True, "type": acc_type, "default_acc_id": dmap.get(acc_type)}

@router.delete("/{acc_id}/credential")
def delete_credential(acc_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        db.delete(acc)
        db.commit()
    return {"ok": True}

def _cache_summary(acc_id: int, data: dict) -> None:
    try:
        cache = get_group("account_summary")
        cache[str(acc_id)] = data
        save_group("account_summary", cache)
    except Exception:
        pass

def _cached_summary(acc_id: int) -> dict:
    try:
        data = get_group("account_summary").get(str(acc_id))
    except Exception:
        data = None
    return data if isinstance(data, dict) else {"capacity": None, "vip": None}

@router.get("/{acc_id}/summary")
def account_summary(acc_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        if acc.status != "connected" or not acc.cookies_enc:
            return {"capacity": None, "vip": None}
        try:
            adapter = make_adapter_for(db, acc.type, acc_id)
        except HTTPException:
            return {"capacity": None, "vip": None}
    try:
        data = adapter.summary()
    except Exception:
        return _cached_summary(acc_id)
    _cache_summary(acc_id, data)
    return data

class NotifyBody(BaseModel):
    enabled: bool = True

@router.put("/{acc_id}/notify")
def set_drive_notify(acc_id: int, body: NotifyBody, _user=CurrentUser):
    with SessionLocal() as db:
        _acc_or_404(db, acc_id)
    cfg = get_group("drive_notify")
    cfg[str(acc_id)] = bool(body.enabled)
    save_group("drive_notify", cfg)
    return {"ok": True, "acc_id": acc_id, "enabled": bool(body.enabled)}

@router.post("/{acc_id}/check")
def check_account(acc_id: int, _user=CurrentUser):
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        if not acc.cookies_enc:
            return {"ok": False, "kind": "warning", "message": "尚未配置凭据，请先配置", "status": "unset", "last_check": "从未配置"}
        try:
            adapter = make_adapter_for(db, acc.type, acc_id)
            nickname = adapter.verify()
            acc.status = "connected"
            acc.nickname = nickname
            acc.last_check = now_str()
            db.commit()
            return {"ok": True, "kind": "success", "message": f"连通正常（{nickname}）", "status": "connected", "last_check": acc.last_check}
        except HTTPException:
            raise
        except Exception as e:
            acc.status = "expired"
            acc.last_check = now_str()
            db.commit()
            return {"ok": False, "kind": "error", "message": f"检测失败：{e}", "status": "expired", "last_check": acc.last_check}
