"""刮削启动看门狗（run_watch.verify_scrape_started）单测。

2026-11-02 小城良方实录：百度转存接口异步返回，队列配置的 10s 后 QMS 扫到空目录，
一条记录都没有，只能人工重触发。这里盯 40 秒，没扫到就自动重触发一次（老大定稿：只一次）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.run_watch as rw  # noqa: E402

NAMES = ["小城良方 (2026).mkv"]


def _clock(monkeypatch):
    c = {"t": 1000.0}
    monkeypatch.setattr(rw.time, "time", lambda: c["t"])
    monkeypatch.setattr(rw.time, "sleep", lambda s: c.__setitem__("t", c["t"] + s))
    return c


def test_records_appear_no_retry(monkeypatch):
    _clock(monkeypatch)
    ticks = {"n": 0}

    def fake_claim(names, since_id=0):
        ticks["n"] += 1
        return {"小城良方 (2026).mkv": {"id": 99}} if ticks["n"] >= 2 else {}

    monkeypatch.setattr(rw, "claim_records", fake_claim)
    monkeypatch.setattr(rw.qms, "scrape_path_status", lambda pid: {"is_running": 0, "is_scraping": False})
    triggers = {"n": 0}
    monkeypatch.setattr(rw.qms, "trigger_scrape", lambda pid: (triggers.__setitem__("n", triggers["n"] + 1), (True, ""))[1])

    logs: list[str] = []
    rw.verify_scrape_started(10, NAMES, {}, log_fn=logs.append)
    assert triggers["n"] == 0
    assert any("已开始处理" in x for x in logs)


def test_nothing_found_retries_exactly_once(monkeypatch):
    _clock(monkeypatch)
    monkeypatch.setattr(rw, "claim_records", lambda names, since_id=0: {})
    monkeypatch.setattr(rw.qms, "scrape_path_status", lambda pid: {"is_running": 0, "is_scraping": False})
    triggers = {"n": 0}

    def fake_trigger(pid):
        triggers["n"] += 1
        return True, ""

    monkeypatch.setattr(rw.qms, "trigger_scrape", fake_trigger)
    logs: list[str] = []
    rw.verify_scrape_started(10, NAMES, {}, log_fn=logs.append, retry_after=40, poll_interval=5)
    assert triggers["n"] == 1  # 只重试一次
    assert any("已重触发一次刮削" in x for x in logs)


def test_scrape_already_running_no_retry(monkeypatch):
    _clock(monkeypatch)
    monkeypatch.setattr(rw, "claim_records", lambda names, since_id=0: {})
    monkeypatch.setattr(rw.qms, "scrape_path_status", lambda pid: {"is_running": 1, "is_scraping": False})
    triggers = {"n": 0}
    monkeypatch.setattr(rw.qms, "trigger_scrape", lambda pid: (triggers.__setitem__("n", triggers["n"] + 1), (True, ""))[1])
    rw.verify_scrape_started(10, NAMES, {})
    assert triggers["n"] == 0
