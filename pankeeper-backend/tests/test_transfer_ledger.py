"""T-002 本地已转存台账测试。

通过 run_manual / run_auto 全流程（make_adapter 打桩，无真实网盘请求）验证：
转存一次落账 → 同分享同文件再跑直接跳过且 save_files 零调用（哨兵，连网盘基线
都不查）→ 清理接口放行；另附 save_dir / task_id 隔离与 MD5 兜底。库用 conftest
的临时库。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.adapters.base import ShareFile, TransferResult
from app.db import SessionLocal
from app.models import Record, TransferLedger
from app.transfer import auto as auto_mod
from app.transfer import manual as manual_mod
from app.transfer.ledger import ledger_prefilter, ledger_write

MD5 = "bb" * 16
SHARE_URL = "https://pan.quark.cn/s/t002test"


@pytest.fixture(autouse=True)
def _clean_ledger():
    with SessionLocal() as s:
        s.query(TransferLedger).delete()
        s.commit()
    yield
    with SessionLocal() as s:
        s.query(TransferLedger).delete()
        s.query(Record).filter(Record.share_url == SHARE_URL).delete()
        s.commit()


def _eng():
    lock = threading.Lock()
    return SimpleNamespace(_lock=lock, _cond=threading.Condition(lock), state={})


class _FakeAdapter:
    """list_share 回放固定清单；save_files 按模式：正常记账 / 哨兵（被调即炸）。"""

    def __init__(self, files, mode="ok"):
        self._files = files
        self.mode = mode
        self.save_calls = 0

    def list_share(self, spec):
        return list(self._files)

    def save_files(self, files, spec, on_progress, on_log):
        if self.mode == "sentinel":
            raise AssertionError("台账应全命中：save_files 不应被调用（不应请求网盘基线）")
        self.save_calls += 1
        on_progress(50)
        result = TransferResult()
        for f in files:
            if not f.is_dir:
                result.add += 1
                result.transferred.append({"name": f.target_name or f.name, "fid": f.fid})
        on_progress(100)
        return result


def _files():
    return [ShareFile(fid="1", name="EP.mkv", path="/EP.mkv", size=1, md5=MD5)]


def test_manual_roundtrip_ledger_skip_offline(monkeypatch):
    """验收 2（手动链路）：转存一次 → 台账有记录（含 MD5/task_id=0）；同分享同文件
    再跑 → 直接跳过，save_files 零调用（哨兵断言网盘零请求）。"""
    fake = _FakeAdapter(_files())
    monkeypatch.setattr(manual_mod, "make_adapter", lambda *a, **k: fake)
    t = {
        "logs": [], "name": "测试剧.S01E01", "type": "quark", "shareUrl": SHARE_URL,
        "shareCode": "", "path": "/待整理", "includeSubdirs": True, "mediaOff": True,
    }

    manual_mod.run_manual(_eng(), t, {})

    assert fake.save_calls == 1
    with SessionLocal() as s:
        rows = s.query(TransferLedger).all()
        assert len(rows) == 1
        r = rows[0]
        assert (r.drive, r.task_id, r.save_dir, r.name, r.md5) == ("quark", 0, "/待整理", "EP.mkv", MD5)

    # 二跑：save_files 装哨兵——台账全命中时绝不能碰网盘
    sentinel = _FakeAdapter(_files(), mode="sentinel")
    monkeypatch.setattr(manual_mod, "make_adapter", lambda *a, **k: sentinel)
    manual_mod.run_manual(_eng(), {"logs": [], **{k: v for k, v in t.items() if k != "logs"}}, {})

    with SessionLocal() as s:
        latest = s.query(Record).filter(Record.share_url == SHARE_URL).order_by(Record.id.desc()).first()
        assert latest.st == "完成 0/1"  # 计入 skip（分母仍是清单里的 1 个文件）
        assert s.query(TransferLedger).count() == 1  # 唯一键兜底：重复落账不涨


def test_auto_roundtrip_ledger_skip_offline(monkeypatch):
    """验收 2（自动链路）：paTaskId 作为 task_id 落账；二跑全命中零网盘请求。"""
    fake = _FakeAdapter(_files())
    monkeypatch.setattr(auto_mod, "make_adapter", lambda *a, **k: fake)
    t = {
        "logs": [], "name": "自动剧.S01", "type": "quark", "shareUrl": SHARE_URL,
        "shareCode": "", "path": "/待整理", "includeSubdirs": True, "paTaskId": 7,
    }

    auto_mod.run_auto(_eng(), t, {})

    assert fake.save_calls == 1
    with SessionLocal() as s:
        r = s.query(TransferLedger).one()
        assert (r.drive, r.task_id, r.save_dir, r.name) == ("quark", 7, "/待整理", "EP.mkv")

    sentinel = _FakeAdapter(_files(), mode="sentinel")
    monkeypatch.setattr(auto_mod, "make_adapter", lambda *a, **k: sentinel)
    auto_mod.run_auto(_eng(), {**t, "logs": []}, {})

    with SessionLocal() as s:
        latest = s.query(Record).filter(Record.share_url == SHARE_URL).order_by(Record.id.desc()).first()
        assert latest.st == "完成 0/1"


def test_ledger_clear_api_unblocks():
    """验收 3：DELETE /api/pa/ledger 按过滤清理 → 台账不再拦截，文件可重转。"""
    result = TransferResult()
    result.transferred = [{"name": "EP.mkv", "fid": "1"}]
    ledger_write("quark", None, 0, SHARE_URL, "/待整理", _files(), result)

    from fastapi.testclient import TestClient

    from app.api.auth import ensure_admin
    from app.main import create_app
    from app.security import make_token

    ensure_admin()
    client = TestClient(create_app())
    token = make_token("admin", 1)

    r = client.delete("/api/pa/ledger", params={"drive": "quark", "name": "EP.mkv"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == {"deleted": 1}

    kept, hits = ledger_prefilter("quark", 0, "/待整理", _files())
    assert hits == [] and len(kept) == 1  # 清掉后台账不再拦截（命中名单为空表）


def test_ledger_save_dir_isolation():
    """验收 4：不同 save_dir（与 task_id）下的同名文件不互相命中台账。"""
    result = TransferResult()
    result.transferred = [{"name": "EP.mkv", "fid": "1"}]
    ledger_write("quark", None, 0, SHARE_URL, "/目录A", _files(), result)

    kept, hits = ledger_prefilter("quark", 0, "/目录B", _files())
    assert hits == [] and len(kept) == 1  # 不同 save_dir：不命中
    kept, hits = ledger_prefilter("quark", 5, "/目录A", _files())
    assert hits == [] and len(kept) == 1  # 不同 task_id：不命中（附验）


def test_ledger_md5_hit_covers_renamed_file():
    """改名文件（名字对不上）靠台账里的 MD5 兜住——不误转。"""
    result = TransferResult()
    result.transferred = [{"name": "旧名.mkv", "fid": "1"}]
    ledger_write(
        "baidu", None, 0, SHARE_URL, "/A",
        [ShareFile(fid="1", name="旧名.mkv", path="/旧名.mkv", size=1, md5=MD5)],
        result,
    )

    renamed = [ShareFile(fid="2", name="新名.mkv", path="/新名.mkv", size=1, md5=MD5)]
    kept, hits = ledger_prefilter("baidu", 0, "/A", renamed)
    assert len(hits) == 1 and kept == []
    assert hits[0]["name"] == "新名.mkv" and hits[0]["md5"] == MD5  # T-017：名单带名字+MD5
