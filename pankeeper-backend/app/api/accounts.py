"""网盘连接：多账号（每平台可配多个，同时在线）。只回状态绝不回明文（红线）。"""
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

# 平台展示元信息（品牌色/凭据形态），与前端 accounts 页一致
META = {
    "baidu": {"short": "百度", "color": "#1677ff", "cred_kind": "Cookie", "note": "适配器开发中（依据 bdsavepro 调研的接口事实）"},
    "quark": {"short": "夸克", "color": "#13c2c2", "cred_kind": "Cookie", "note": "已实现：转存/清单/目录/重命名"},
    "115": {"short": "115", "color": "#722ed1", "cred_kind": "Cookie / 扫码", "note": "适配器开发中（p115client 方案）"},
}
ORDER = ("baidu", "quark", "115")


def _acc_or_404(db, acc_id: int) -> Account:
    acc = db.get(Account, acc_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return acc


def _row(acc: Account) -> dict:
    """单账号卡片行：平台静态元信息 + 账号动态状态。"""
    meta = META.get(acc.type, {"short": acc.type, "color": "#888", "cred_kind": "Cookie", "note": ""})
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
        # 卡片上的「失效通知」开关（Server 酱）：按账号存；没配凭据的账号
        # 没有"失效"可言 —— 展示为关（配置后自动恢复开关原值）
        "notify": bool(acc.cookies_enc) and drive_enabled(str(acc.id)),
        # 容量/会员摘要走缓存：刷新页面能立刻显示，不必等实时请求
        "summary": _cached_summary(acc.id),
    }


@router.get("")
def list_accounts(_user=CurrentUser):
    out = []
    with SessionLocal() as db:
        rows = db.query(Account).order_by(Account.type, Account.id).all()
        for acc in rows:
            out.append(_row(acc))
    return out


class CredentialBody(BaseModel):
    cookies: str
    alias: str = ""


class AliasBody(BaseModel):
    alias: str = ""


def _save_and_verify(acc: Account, cookies: str) -> dict:
    """写入凭据 → 保存即验证。成功置 connected 并回昵称；失败置 expired 并抛 400。"""
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
    """新增账号（选平台 + 粘贴 Cookie + 可选别名）。保存即验证。"""
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
    """删除整个账号（卡片随之消失）。"""
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        db.delete(acc)
        db.commit()
    return {"ok": True}


@router.delete("/{acc_id}/credential")
def delete_credential(acc_id: int, _user=CurrentUser):
    """清空凭据。没有凭据的空壳卡片没有存在意义（前端不展示）——直接删整个账号，
    与 DELETE /{acc_id} 同义；保留端点只为兼容旧调用方。"""
    with SessionLocal() as db:
        acc = _acc_or_404(db, acc_id)
        db.delete(acc)
        db.commit()
    return {"ok": True}


def _cache_summary(acc_id: int, data: dict) -> None:
    """把容量/会员摘要落进 settings（key: account_summary，按账号 id）。

    卡片刷新时先拿缓存渲染，不必干等一次实时请求；实时结果回来再覆盖。
    缓存失败绝不影响接口本身。
    """
    try:
        cache = get_group("account_summary")
        cache[str(acc_id)] = data
        save_group("account_summary", cache)
    except Exception:  # noqa: BLE001
        pass


def _cached_summary(acc_id: int) -> dict:
    """读缓存摘要（没有则返回空壳，前端显示「暂无」）。"""
    try:
        data = get_group("account_summary").get(str(acc_id))
    except Exception:  # noqa: BLE001
        data = None
    return data if isinstance(data, dict) else {"capacity": None, "vip": None}


@router.get("/{acc_id}/summary")
def account_summary(acc_id: int, _user=CurrentUser):
    """容量 + 会员摘要（卡片上的容量条/会员标签数据源）。

    原则同凭据红线：只回摘要数字，不回任何凭据内容。
    未配置/不支持/获取失败统一返回 null 字段，前端显示「暂无」。
    成功时写缓存，页面刷新即可立现（不必重打网盘接口）。
    """
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
    """账号粒度的「失效通知」开关（网盘连接页卡片上用）。

    只是粒度控制——探活发现失效时先看这里，再走 notify.push（那里还有总闸
    settings.notify.enabled 与 on_cred 时机开关）。
    """
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
