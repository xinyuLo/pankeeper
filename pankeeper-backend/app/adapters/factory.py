from __future__ import annotations

from ..db import SessionLocal
from ..models import Account
from .base import AdapterError, CloudAdapter
from .baidu import BaiduClient
from .pan115 import Pan115Adapter
from .quark import QuarkAdapter

ADAPTERS: dict[str, type[CloudAdapter]] = {"quark": QuarkAdapter, "baidu": BaiduClient, "115": Pan115Adapter}

def make_adapter(drive_type: str, acc_id: int | None = None) -> CloudAdapter:
    cls = ADAPTERS.get(drive_type)
    if cls is None:
        raise AdapterError(f"网盘 {drive_type} 的适配器尚未实现（当前支持：{'/'.join(ADAPTERS)}）")
    with SessionLocal() as s:
        if acc_id is not None:
            acc = s.get(Account, acc_id)
            if acc is None or acc.type != drive_type:
                raise AdapterError("转存任务指定的账号不存在")
        else:
            acc = s.query(Account).filter(Account.type == drive_type).order_by(Account.id).first()

    if acc is None or not acc.cookies_enc:
        raise AdapterError(f"{drive_type} 账号未配置凭据，请先到「网盘连接」绑定")
    return cls(acc.cookies_enc)
