"""API Key 测试：服务层生成/校验/CRUD + HTTP 层认证接入 + 改密码禁区。

TestClient 的来源 IP（testclient）不是回环 → 本机豁免不生效，走完整鉴权链，
正好覆盖「外部调用」的真实路径。

运行：python -m pytest tests/test_apikey.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-apikey-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402

init_db()

from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import ensure_admin  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import make_token  # noqa: E402
from app.services import apikey_svc  # noqa: E402

ensure_admin()
client = TestClient(create_app())
TOKEN = make_token("admin", 1)


# ---------- 服务层：生成 / 哈希 / 校验 ----------

def test_generate_key_format():
    raw = apikey_svc.generate_raw_key()
    assert raw.startswith("pk_")
    body = raw[3:]
    assert len(body) == 24 and body.isalnum()
    # 未入库的明文不认
    assert apikey_svc.validate(raw) is None


def test_create_stores_hash_and_prefix_only():
    row, raw = apikey_svc.create("单测-哈希")
    assert row.key_prefix == raw[:8]
    assert row.key_hash == apikey_svc.hash_key(raw)
    assert raw not in row.key_hash  # 库里只有哈希，绝不存明文
    assert row.is_active is True
    assert row.created_at > 0
    hit = apikey_svc.validate(raw)
    assert hit is not None and hit.id == row.id
    # 错 key / 空 key 一律 None
    assert apikey_svc.validate("pk_" + "x" * 24) is None
    assert apikey_svc.validate("") is None


def test_status_toggle_gates_validation():
    row, raw = apikey_svc.create("单测-停用")
    assert apikey_svc.validate(raw) is not None
    assert apikey_svc.set_status(row.id, False)
    assert apikey_svc.validate(raw) is None  # 停用立即失效
    assert apikey_svc.set_status(row.id, True)
    assert apikey_svc.validate(raw) is not None


def test_delete():
    row, raw = apikey_svc.create("单测-删除")
    assert apikey_svc.delete(row.id) is True
    assert apikey_svc.delete(row.id) is False  # 二次删不存在
    assert apikey_svc.validate(raw) is None


def test_list_newest_first():
    a, _ = apikey_svc.create("单测-列表A")
    b, _ = apikey_svc.create("单测-列表B")
    ids = [r.id for r in apikey_svc.list_all()]
    assert ids.index(b.id) < ids.index(a.id)  # 新建的排前面


# ---------- HTTP 层：中间件 + 依赖（非回环来源，走完整鉴权链） ----------

def test_http_without_any_credential_is_401():
    assert client.get("/api/apikeys").status_code == 401


def test_http_x_api_key_header():
    _, raw = apikey_svc.create("单测-请求头")
    r = client.get("/api/apikeys", headers={"X-API-Key": raw})
    assert r.status_code == 200
    assert any(i["name"] == "单测-请求头" for i in r.json()["items"])
    # 列表里绝不出现明文 key / 哈希
    assert all("key" not in i and "key_hash" not in i for i in r.json()["items"])


def test_http_api_key_query_param():
    _, raw = apikey_svc.create("单测-URL参数")
    r = client.get("/api/apikeys", params={"api_key": raw})
    assert r.status_code == 200


def test_http_invalid_key_falls_back_to_token_check():
    r = client.get("/api/apikeys", headers={"X-API-Key": "pk_" + "z" * 24})
    assert r.status_code == 401  # key 无效且无 token → 401


def test_http_token_still_works():
    r = client.get("/api/apikeys", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_http_disabled_key_is_401():
    row, raw = apikey_svc.create("单测-失效")
    apikey_svc.set_status(row.id, False)
    assert client.get("/api/apikeys", headers={"X-API-Key": raw}).status_code == 401


# ---------- 改密码是 API Key 唯一禁区 ----------

def test_password_change_forbidden_via_api_key():
    _, raw = apikey_svc.create("单测-改密")
    r = client.put(
        "/api/settings/security",
        json={"username": "admin"},
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 403


def test_password_change_allowed_via_token():
    r = client.put(
        "/api/settings/security",
        json={"username": "admin"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
