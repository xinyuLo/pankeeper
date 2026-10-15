"""缓存到期自动预热的到期判定（2026-10-15）：开关/TTL/单位/触发与记账。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.cache_warm as cw  # noqa: E402

HOUR = 3600


def _cfg(master=True, auto=True, ttl=30, unit="小时"):
    return {"master": master, "auto": auto, "ttl": ttl, "ttlUnit": unit}


def _env(monkeypatch, cfg: dict, last: int) -> dict:
    """接管配置组与持久化，返回捕获到的 save_group 写入。"""
    saved: dict = {}
    monkeypatch.setattr(cw, "get_group", lambda g: dict(cfg) if g == "cache_cfg" else {"last": last})
    monkeypatch.setattr(cw, "_latest_cached_at", lambda: 0)

    def fake_save(group: str, value: dict) -> None:
        if group == cw.WARM_GROUP:
            saved.update(value)

    monkeypatch.setattr(cw, "save_group", fake_save)
    return saved


def test_master_off_never_fires(monkeypatch):
    _env(monkeypatch, _cfg(master=False), 0)
    assert cw.check_and_fire(now=1000000.0, fire=lambda: 3)["fired"] is False


def test_auto_off_never_fires(monkeypatch):
    _env(monkeypatch, _cfg(auto=False), 0)
    assert cw.check_and_fire(now=1000000.0, fire=lambda: 3)["fired"] is False


def test_not_due_within_ttl(monkeypatch):
    _env(monkeypatch, _cfg(ttl=30), last=int(1000000.0 - 29 * HOUR))
    r = cw.check_and_fire(now=1000000.0, fire=lambda: 3)
    assert r["fired"] is False
    assert 50 <= r["next_in_min"] <= 70  # 差 1 小时到期


def test_fires_when_ttl_elapsed_and_records_time(monkeypatch):
    now = 1000000.0
    saved = _env(monkeypatch, _cfg(ttl=30), last=int(now - 31 * HOUR))
    fired = {"n": 0}

    def fire() -> int:
        fired["n"] += 1
        return 4

    r = cw.check_and_fire(now=now, fire=fire)
    assert r == {"due": True, "fired": True, "accounts": 4}
    assert fired["n"] == 1
    assert saved.get("last") == int(now)  # 触发即记账，下轮从新 TTL 起算


def test_ttl_unit_minutes(monkeypatch):
    now = 1000000.0
    _env(monkeypatch, _cfg(ttl=45, unit="分钟"), last=int(now - 46 * 60))
    assert cw.check_and_fire(now=now, fire=lambda: 2)["fired"] is True
