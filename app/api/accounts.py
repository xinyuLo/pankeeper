"""网盘连接：只回状态绝不回明文（红线）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import CurrentUser, make_adapter_for
from ..models import Account, now_str
from ..security import encrypt_credential

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# 展示元信息（品牌色/凭据形态），与前端 accounts 页一致
META = {
    "baidu": {"short": "百度", "color": "#1677ff", "cred_kind": "BDUSS / STOKEN", "note": "适配器开发中（依据 bdsavepro 调研的接口事实）"},
    "quark": {"short": "夸克", "color": "#13c2c2", "cred_kind": "Cookie", "note": "已实现：转存/清单/目录/重命名"},
    "115": {"short": "115", "color": "#722ed1", "cred_kind": "Cookie / 扫码", "note": "适配器开发中（p115client 方案）"},
}
ORDER = ("baidu", "quark", "115")


@router.get("")
def list_accounts(_user=CurrentUser):
    out = []
    with SessionLocal() as db:
        for t in ORDER:
            acc = db.get(Account, t)
            meta = META[t]
            out.append(
                {
                    "type": t,
                    "short": meta["short"],
                    "color": meta["color"],
                    "cred_kind": meta["cred_kind"],
                    "note": meta["note"],
                    "status": acc.status if acc else "unset",
                    "nickname": acc.nickname if acc else "",
                    "last_check": acc.last_check if acc else "从未配置",
                }
            )
    return out


class CredentialBody(BaseModel):
    cookies: str


@router.put("/{drive_type}/credential")
def put_credential(drive_type: str, body: CredentialBody, _user=CurrentUser):
    if drive_type not in ORDER:
        raise HTTPException(status_code=404, detail="未知网盘")
    adapter = None
    with SessionLocal() as db:
        acc = db.get(Account, drive_type)
        if acc is None:
            acc = Account(type=drive_type)
            db.add(acc)
        acc.cookies_enc = encrypt_credential(body.cookies.strip())
        acc.last_check = "从未配置"
        db.commit()
    # 保存即验证：失效直接报错并标记 expired（只回状态不回明文）
    from ..db import SessionLocal as S2

    try:
        with S2() as db:
            adapter = make_adapter_for(db, drive_type)
            nickname = adapter.verify()
        with S2() as db:
            acc = db.get(Account, drive_type)
            acc.status = "connected"
            acc.nickname = nickname
            acc.last_check = now_str()
            db.commit()
        return {"ok": True, "status": "connected", "nickname": nickname}
    except HTTPException:
        raise
    except Exception as e:
        with S2() as db:
            acc = db.get(Account, drive_type)
            if acc:
                acc.status = "expired"
                acc.last_check = now_str()
                db.commit()
        raise HTTPException(status_code=400, detail=f"凭据验证失败：{e}")


@router.delete("/{drive_type}/credential")
def delete_credential(drive_type: str, _user=CurrentUser):
    with SessionLocal() as db:
        acc = db.get(Account, drive_type)
        if acc:
            acc.cookies_enc = ""
            acc.status = "unset"
            acc.nickname = ""
            acc.last_check = "从未配置"
            db.commit()
    return {"ok": True}


@router.get("/{drive_type}/summary")
def account_summary(drive_type: str, _user=CurrentUser):
    """容量 + 会员摘要（卡片上的容量条数据源）。

    原则同凭据红线：只回摘要数字，不回任何凭据内容。
    未配置/不支持/获取失败统一返回 null 字段，前端显示「暂无」。
    """
    if drive_type not in ORDER:
        raise HTTPException(status_code=404, detail="未知网盘")
    with SessionLocal() as db:
        acc = db.get(Account, drive_type)
        if acc is None or acc.status != "connected" or not acc.cookies_enc:
            return {"capacity": None, "vip": None}
        try:
            adapter = make_adapter_for(db, drive_type)
        except HTTPException:
            return {"capacity": None, "vip": None}
    try:
        return adapter.summary()
    except Exception:
        return {"capacity": None, "vip": None}


@router.post("/{drive_type}/check")
def check_account(drive_type: str, _user=CurrentUser):
    with SessionLocal() as db:
        acc = db.get(Account, drive_type)
        if acc is None or not acc.cookies_enc:
            return {"ok": False, "kind": "warning", "message": "尚未配置凭据，请先配置", "status": "unset", "last_check": "从未配置"}
        try:
            adapter = make_adapter_for(db, drive_type)
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
