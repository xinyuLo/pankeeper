"""API Key 服务：生成 / 哈希校验 / CRUD / 最后使用时间回写。

对齐 QMS 的实现口径：完整密钥只返回一次，库里只存 SHA256；列表展示前 8 位明文前缀；
last_used_at 走后台线程回写（请求路径上不做同步写，别让一次 UPDATE 拖住响应，
也不和转存主流程抢 SQLite 写锁）。
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

from ..db import SessionLocal
from ..models import ApiKey

# pk_ + 24 位随机字母数字（QMS 是 qms_ + 24，同长度同字符集，只换前缀防串用）
_PREFIX = "pk_"
_RANDOM_LEN = 24
_CHARSET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def generate_raw_key() -> str:
    return _PREFIX + "".join(secrets.choice(_CHARSET) for _ in range(_RANDOM_LEN))


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(name: str) -> tuple[ApiKey, str]:
    """新建 Key，返回 (行, 完整明文密钥)——明文仅此一次，调用方直接进响应。"""
    raw = generate_raw_key()
    row = ApiKey(
        name=name,
        key_hash=hash_key(raw),
        key_prefix=raw[:8],
        is_active=True,
        created_at=int(time.time()),
    )
    with SessionLocal() as s:
        s.add(row)
        s.commit()
    return row, raw


def list_all() -> list[ApiKey]:
    with SessionLocal() as s:
        return s.query(ApiKey).order_by(ApiKey.id.desc()).all()


def set_status(key_id: int, is_active: bool) -> bool:
    with SessionLocal() as s:
        row = s.get(ApiKey, key_id)
        if row is None:
            return False
        row.is_active = is_active
        s.commit()
        return True


def delete(key_id: int) -> bool:
    with SessionLocal() as s:
        row = s.get(ApiKey, key_id)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def validate(raw: str) -> ApiKey | None:
    """明文 → 启用中的 Key 行；没命中返回 None（调用方回落 token 校验）。"""
    if not raw:
        return None
    with SessionLocal() as s:
        return s.query(ApiKey).filter(ApiKey.key_hash == hash_key(raw), ApiKey.is_active.is_(True)).first()


def touch_later(key_id: int) -> None:
    """后台线程回写 last_used_at。失败只打日志——统计字段不值得影响请求。"""

    def work() -> None:
        try:
            with SessionLocal() as s:
                row = s.get(ApiKey, key_id)
                if row is not None:
                    row.last_used_at = int(time.time())
                    s.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[apikey] last_used 回写失败（id={key_id}）：{e}")

    threading.Thread(target=work, daemon=True).start()
