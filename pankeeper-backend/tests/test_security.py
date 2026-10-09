from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-sec-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db

init_db()

from app.api.auth import (
    LOCK_AFTER,
    _clear,
    _locked_seconds,
    _register_fail,
    ensure_admin,
)
from app.security import hash_password
from app.services.settings_svc import get_group, save_group

import json

from app.db import SessionLocal
from app.models import Setting

ensure_admin()

def test_lock_after_max_failures():
    key = "test-ip-1"
    _clear(key)
    for _ in range(LOCK_AFTER - 1):
        assert _locked_seconds(key) == 0
        _register_fail(key)
    assert _locked_seconds(key) == 0
    _register_fail(key)
    assert _locked_seconds(key) > 0

def test_lock_clears_on_success():
    key = "test-ip-2"
    _clear(key)
    _register_fail(key)
    _register_fail(key)
    _clear(key)
    assert _locked_seconds(key) == 0

def test_sensitive_fields_encrypted_at_rest():
    save_group("settings", {
        **get_group("settings"),
        "notify": {**get_group("settings")["notify"], "sendkey": "SCT_secret_123"},
        "qms": {**get_group("settings")["qms"], "apikey": "qms-key-abc"},
    })
    with SessionLocal() as s:
        raw = s.get(Setting, "settings").value_json

    assert "SCT_secret_123" not in raw
    assert "qms-key-abc" not in raw
    assert raw.count("fernet:") >= 2

    group = get_group("settings")
    assert group["notify"]["sendkey"] == "SCT_secret_123"
    assert group["qms"]["apikey"] == "qms-key-abc"

def test_masked_roundtrip_keeps_old_secret():
    save_group("settings", {
        **get_group("settings"),
        "notify": {**get_group("settings")["notify"], "sendkey": "SCT_keep_me"},
    })
    cur = get_group("settings")
    cur["notify"]["sendkey"] = "****me"
    save_group("settings", cur)
    assert get_group("settings")["notify"]["sendkey"] == "SCT_keep_me"

def test_admin_password_hashed_not_plaintext():
    with SessionLocal() as s:
        raw = s.get(Setting, "admin").value_json
    assert "admin#123" not in raw
    assert "pbkdf2$" in raw
