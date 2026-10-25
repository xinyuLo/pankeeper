"""预热队列分道（2026-10-15）：同网盘多账号串行（防同家风控），跨网盘并行。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.api.cache_api as ca  # noqa: E402


def _reset() -> None:
    with ca._WARM_LOCK:
        ca._WARM_JOBS.clear()
        ca._WARM_QUEUES.clear()
        ca._WARM_WORKERS.clear()
        ca._WARM_CBS.clear()
        ca._WARM_GATES.clear()


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def test_same_drive_serial_cross_drive_parallel(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_walk(type, acc_id, job, force=False, gate=None):
        job["status"] = "running"
        if type == "baidu":
            started.set()
            release.wait(5)  # 挂住百度这条道
        job["status"] = "done"

    monkeypatch.setattr(ca, "_warm_walk", fake_walk)
    monkeypatch.setattr(ca, "_acc_name", lambda t, a: f"{t}#{a}")

    j1 = ca._enqueue_warm("baidu", 1)
    assert started.wait(3), "百度任务应已开跑"

    # 夸克不等百度：百度还挂着时，夸克走自己的线程跑到 done
    j2 = ca._enqueue_warm("quark", None)
    deadline = time.time() + 3
    while j2["status"] != "done" and time.time() < deadline:
        time.sleep(0.01)
    assert j2["status"] == "done"
    assert j1["status"] == "running"

    # 同盘第二个百度账号必须排队（串行），release 后接续跑完
    j3 = ca._enqueue_warm("baidu", 2)
    assert j3["status"] == "queued"
    release.set()
    for job in (j1, j3):
        deadline = time.time() + 3
        while job["status"] != "done" and time.time() < deadline:
            time.sleep(0.01)
        assert job["status"] == "done"


def test_same_drive_fifo_order(monkeypatch):
    order: list[str] = []

    def fake_walk(type, acc_id, job, force=False, gate=None):
        job["status"] = "running"
        order.append(acc_id)
        job["status"] = "done"

    monkeypatch.setattr(ca, "_warm_walk", fake_walk)
    monkeypatch.setattr(ca, "_acc_name", lambda t, a: f"{t}#{a}")

    ca._enqueue_warm("baidu", 1)
    ca._enqueue_warm("baidu", 2)
    deadline = time.time() + 3
    while len(order) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert order == [1, 2]  # 同盘严格 FIFO
