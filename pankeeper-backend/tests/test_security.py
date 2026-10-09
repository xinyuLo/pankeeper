"""安全防护测试：登录防暴力锁定 + 敏感设置字段落库加密。

运行：.venv/Scripts/python -m pytest tests/test_security.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-sec-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402

init_db()

from app.api.auth import (  # noqa: E402
    LOCK_AFTER,
    _clear,
    _locked_seconds,
    _register_fail,
    ensure_admin,
)
from app.security import hash_password  # noqa: E402
from app.services.settings_svc import get_group, save_group  # noqa: E402

import json  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Setting  # noqa: E402

ensure_admin()


# ---------- 登录防暴力锁定 ----------

def test_lock_after_max_failures():
    key = "test-ip-1"
    _clear(key)
    for _ in range(LOCK_AFTER - 1):
        assert _locked_seconds(key) == 0  # 未到阈值前不锁
        _register_fail(key)
    assert _locked_seconds(key) == 0  # 第 4 次失败还不锁
    _register_fail(key)  # 第 5 次 → 锁定
    assert _locked_seconds(key) > 0


def test_lock_clears_on_success():
    key = "test-ip-2"
    _clear(key)
    _register_fail(key)
    _register_fail(key)
    _clear(key)  # 登录成功即清零
    assert _locked_seconds(key) == 0


# ---------- 敏感设置字段落库加密 ----------

def test_sensitive_fields_encrypted_at_rest():
    save_group("settings", {
        **get_group("settings"),
        "notify": {**get_group("settings")["notify"], "sendkey": "SCT_secret_123"},
        "qms": {**get_group("settings")["qms"], "apikey": "qms-key-abc"},
    })
    with SessionLocal() as s:
        raw = s.get(Setting, "settings").value_json
    # 落库的是 Fernet 密文（fernet: 前缀），明文绝不出现在磁盘
    assert "SCT_secret_123" not in raw
    assert "qms-key-abc" not in raw
    assert raw.count("fernet:") >= 2
    # 读回来自动解密，业务拿到的还是明文
    group = get_group("settings")
    assert group["notify"]["sendkey"] == "SCT_secret_123"
    assert group["qms"]["apikey"] == "qms-key-abc"


def test_masked_roundtrip_keeps_old_secret():
    """前端回传 **** 掩码：库里旧明文密文都应保留。"""
    save_group("settings", {
        **get_group("settings"),
        "notify": {**get_group("settings")["notify"], "sendkey": "SCT_keep_me"},
    })
    cur = get_group("settings")
    cur["notify"]["sendkey"] = "****me"  # 前端掩码
    save_group("settings", cur)
    assert get_group("settings")["notify"]["sendkey"] == "SCT_keep_me"


# ---------- 管理员哈希落库形态 ----------

def test_admin_password_hashed_not_plaintext():
    with SessionLocal() as s:
        raw = s.get(Setting, "admin").value_json
    assert "admin#123" not in raw
    assert "pbkdf2$" in raw
