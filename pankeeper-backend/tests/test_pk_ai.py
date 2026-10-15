"""AI 专用层 /api/ai/* 测试：API Key 白名单 + 只读端点 + transfer 判断闸。

TestClient 的来源 IP（testclient）不是回环 → 本机豁免不生效，走完整鉴权链，
正好覆盖「外部 AI 调用方」的真实路径（与 test_apikey.py 同一套路数）。

运行：python -m pytest tests/test_pk_ai.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-pkai-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402

init_db()

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import factory as adapter_factory  # noqa: E402
from app.adapters.base import ShareBanned, ShareFile, ShareLocked  # noqa: E402
from app.api.auth import ensure_admin  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Account, DdItem  # noqa: E402
from app.queue import engine as engine_mod  # noqa: E402
from app.security import make_token  # noqa: E402
from app.services import apikey_svc  # noqa: E402

ensure_admin()
client = TestClient(create_app())
TOKEN = make_token("admin", 1)
_, AI_KEY = apikey_svc.create("单测-AI层")
KEY_H = {"X-API-Key": AI_KEY}
TOKEN_H = {"Authorization": f"Bearer {TOKEN}"}
DD_PATH = "/影视/待整理-电影"

# 种子：夸克账号 + 夸克转存目录 + 一个百度账号（bad_acc 用例）——preflight drives 以账号为准
with SessionLocal() as s:
    if not s.query(Account).filter(Account.type == "quark").first():
        s.add(Account(type="quark", alias="夸克测试号"))
    if s.query(DdItem).filter(DdItem.type == "quark").count() == 0:
        s.add(DdItem(type="quark", account="main", name="待整理-电影", path=DD_PATH))
    if not s.query(Account).filter(Account.type == "baidu").first():
        s.add(Account(type="baidu", alias="百度测试号"))
    s.commit()
    BAIDU_ACC_ID = s.query(Account).filter(Account.type == "baidu").first().id


class _FakeAdapter:
    """按 mode 模拟网盘探活/清单：ok / bad（死链）/ locked（缺码）。"""

    mode = "ok"
    FILES = [ShareFile(fid="1", name="Movie.4K.mkv", is_dir=False, size=20 * 1024**3, path="Movie.4K.mkv")]

    def _files(self):
        if _FakeAdapter.mode == "bad":
            raise ShareBanned("分享已取消")
        if _FakeAdapter.mode == "locked":
            raise ShareLocked("该分享需要提取码")
        return list(_FakeAdapter.FILES)

    def probe_share(self, spec):
        return self._files()

    def list_share(self, spec):
        return self._files()


class _StubEngine:
    """替代真引擎：入队只记数，不启 worker（测试里转存不真跑）。"""

    def __init__(self):
        self.items: list[dict] = []

    def enqueue(self, item: dict) -> int:
        self.items.append(item)
        return len(self.items)

    def state_public(self) -> dict:
        return {"seq": len(self.items), "tasks": list(self.items)}


def _patch(monkeypatch, mode: str) -> _StubEngine:
    _FakeAdapter.mode = mode
    monkeypatch.setattr(adapter_factory, "make_adapter", lambda t: _FakeAdapter())
    stub = _StubEngine()
    monkeypatch.setattr(engine_mod, "get_engine", lambda: stub)
    return stub


# ---------- 鉴权白名单 ----------

def test_no_cred_401_on_ai_layer():
    assert client.get("/api/ai/preflight").status_code == 401


def test_api_key_allowed_on_ai_layer():
    r = client.get("/api/ai/preflight", headers=KEY_H)
    assert r.status_code == 200
    drives = {d["type"]: d for d in r.json()["drives"]}
    assert drives["quark"]["dd_configured"] is True
    assert DD_PATH in drives["quark"]["dd_paths"]


def test_api_key_blocked_everywhere_else():
    for path in ("/api/accounts", "/api/records", "/api/queue/state", "/api/apikeys"):
        assert client.get(path, headers=KEY_H).status_code == 403, path
    # 附带收益：接口文档对 Key 关死
    assert client.get("/docs", headers=KEY_H).status_code == 403
    assert client.get("/openapi.json", headers=KEY_H).status_code == 403


def test_token_still_full_access():
    assert client.get("/api/accounts", headers=TOKEN_H).status_code == 200
    assert client.get("/api/ai/preflight", headers=TOKEN_H).status_code == 200


# ---------- check：locked 真实现 + has_code ----------

def test_check_locked_with_has_code(monkeypatch):
    _patch(monkeypatch, "locked")
    r = client.post("/api/ai/check", headers=KEY_H,
                    json={"type": "quark", "url": "https://pan.quark.cn/s/lockedneed"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "locked" and body["has_code"] is False
    r = client.post("/api/ai/check", headers=KEY_H,
                    json={"type": "quark", "url": "https://pan.quark.cn/s/lockedhave", "share_code": "ab12"})
    assert r.json()["has_code"] is True


def test_check_bad(monkeypatch):
    _patch(monkeypatch, "bad")
    r = client.post("/api/ai/check", headers=KEY_H,
                    json={"type": "quark", "url": "https://pan.quark.cn/s/deadlink"})
    assert r.json()["state"] == "bad"


# ---------- transfer 判断闸 ----------

def _payload(url: str, **kw) -> dict:
    base = {
        "name": "测试电影", "type": "quark", "path": DD_PATH,
        "share_url": url, "share_code": "",
        "rename": "测试电影 (2026)", "with_shell": True,
        "file_paths": ["Movie.4K.mkv"],
    }
    base.update(kw)
    return base


def test_transfer_ok(monkeypatch):
    stub = _patch(monkeypatch, "ok")
    r = client.post("/api/ai/transfer", headers=KEY_H, json=_payload("https://pan.quark.cn/s/okcase"))
    assert r.status_code == 200
    body = r.json()
    assert body["pos"] == 1 and body["total_files"] == 1
    assert stub.items[0]["with_shell"] is True and stub.items[0]["rename"] == "测试电影 (2026)"


def test_transfer_bad_link_rejected_without_enqueue(monkeypatch):
    stub = _patch(monkeypatch, "bad")
    r = client.post("/api/ai/transfer", headers=KEY_H, json=_payload("https://pan.quark.cn/s/tbad"))
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "bad_link"
    assert stub.items == []  # 闸拒：不入队（也就不会有失败推送）


def test_transfer_need_code_rejected(monkeypatch):
    stub = _patch(monkeypatch, "locked")
    r = client.post("/api/ai/transfer", headers=KEY_H, json=_payload("https://pan.quark.cn/s/tlock"))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason"] == "need_code"
    assert "提取码" in detail["message"]
    assert stub.items == []


def test_transfer_file_missing_rejected(monkeypatch):
    stub = _patch(monkeypatch, "ok")
    r = client.post("/api/ai/transfer", headers=KEY_H,
                    json=_payload("https://pan.quark.cn/s/tmiss", file_paths=["不存在的文件.mkv"]))
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "file_missing"
    assert stub.items == []


def test_transfer_param_gates():
    # ① type 没配转存目录 → no_dd（baidu 没种子行）
    r = client.post("/api/ai/transfer", headers=KEY_H,
                    json=_payload("https://pan.baidu.com/s/g1", type="baidu", path="/x"))
    assert (r.status_code, r.json()["detail"]["reason"]) == (422, "no_dd")
    # ② path 不是该盘已配置目录 → bad_path
    r = client.post("/api/ai/transfer", headers=KEY_H,
                    json=_payload("https://pan.quark.cn/s/g2", path="/别的目录"))
    assert (r.status_code, r.json()["detail"]["reason"]) == (422, "bad_path")
    # ③ 建壳没带壳名 → no_rename
    r = client.post("/api/ai/transfer", headers=KEY_H,
                    json=_payload("https://pan.quark.cn/s/g3", rename=""))
    assert (r.status_code, r.json()["detail"]["reason"]) == (422, "no_rename")
    # ④ acc_id 不属于该 type → bad_acc
    r = client.post("/api/ai/transfer", headers=KEY_H,
                    json=_payload("https://pan.quark.cn/s/g4", acc_id=BAIDU_ACC_ID))
    assert (r.status_code, r.json()["detail"]["reason"]) == (422, "bad_acc")


# ---------- 其余只读端点 ----------

def test_search_drive_grouping(monkeypatch):
    """drives 参数 → 服务端过滤分组：storable=已配置盘，magnets 留着，未知盘计数丢弃。"""
    from app.api import search as search_api

    rows = [
        {"n": "a", "t": "quark", "url": "u1"},
        {"n": "b", "t": "baidu", "url": "u2"},
        {"n": "c", "t": "magnet", "url": "m1"},
        {"n": "d", "t": "xunlei", "url": "u3"},
    ]
    monkeypatch.setattr(search_api, "search_results", lambda kw, refresh=False: rows)
    r = client.get("/api/ai/search", headers=KEY_H, params={"kw": "测试", "drives": "quark,115"})
    body = r.json()
    assert [x["n"] for x in body["storable"]] == ["a"]
    assert [x["n"] for x in body["magnets"]] == ["c"]
    assert body["dropped"] == 2 and body["drives"] == ["115", "quark"]  # baidu 不在 drives 里也算丢弃


def test_history_and_queue_state_shapes():
    r = client.get("/api/ai/history?limit=5", headers=KEY_H)
    assert r.status_code == 200
    assert "recent_keywords" in r.json() and "recent_transfers" in r.json()
    r = client.get("/api/ai/queue-state", headers=KEY_H)
    assert r.status_code == 200
    assert "seq" in r.json() and isinstance(r.json()["tasks"], list)
