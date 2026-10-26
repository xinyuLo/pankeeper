"""T-006/T-016 · run_task 透出真实队列任务 id（执行监控按它精确匹配本轮）。

T-016：此前 run_task 把 enqueue() 的排队位次当 queue_task_id 回给前端——位次（通常
1）和队列任务 id 对不上，监控弹窗永远匹配不到 → 日志空白 + 10 秒误报「未在队列中
找到」。引擎补 enqueue_id() 返回真实 id；enqueue() 位次语义不变（搜索转存 toast 在用）。
"""
from __future__ import annotations

import time

from app.db import SessionLocal
from app.models import PaTask
from app.queue import engine as engine_mod
from app.queue.engine import QueueEngine
from app.services import pa_scheduler


class _FakeEngine:
    """snapshot 空（无并发同链接任务）；enqueue_id 记录入参并返回预设 id。"""

    def __init__(self, qid: int):
        self.qid = qid
        self.items: list[dict] = []

    def snapshot(self):
        return {"tasks": []}

    def enqueue_id(self, item: dict) -> int:
        self.items.append(item)
        return self.qid


def _make_task() -> int:
    with SessionLocal() as s:
        t = PaTask(type="quark", name="T006测试", share_url="https://pan.quark.cn/s/t006test", save_dir="/x")
        s.add(t)
        s.commit()
        return t.id


def _drop(task_id: int) -> None:
    with SessionLocal() as s:
        row = s.get(PaTask, task_id)
        if row is not None:
            s.delete(row)
        s.commit()


def test_run_task_returns_queue_task_id(monkeypatch):
    """验收：返回体带 queue_task_id 且等于 enqueue_id 的返回值；入参带 pa_task_id。"""
    tid = _make_task()
    try:
        eng = _FakeEngine(42)
        monkeypatch.setattr(engine_mod, "get_engine", lambda: eng)

        res = pa_scheduler.run_task(tid, force=True)

        assert res["queued"] is True
        assert res["queue_task_id"] == 42
        assert eng.items and eng.items[0]["pa_task_id"] == tid
    finally:
        _drop(tid)


def test_run_task_enqueue_dedup_reports_not_queued(monkeypatch):
    """引擎侧同链接去重（enqueue_id 返回 -1，snapshot 检查与入队之间的竞态窗口）：
    如实报未入队，不再误报 queued=True、也不带 queue_task_id。"""
    tid = _make_task()
    try:
        eng = _FakeEngine(-1)
        monkeypatch.setattr(engine_mod, "get_engine", lambda: eng)

        res = pa_scheduler.run_task(tid, force=True)

        assert res["queued"] is False
        assert "已在队列中" in res["reason"]
        assert "queue_task_id" not in res
    finally:
        _drop(tid)


# ---------- T-016 · 真实引擎：enqueue_id 回 id、enqueue 回位次、两者不混用 ----------


def _hermetic_engine() -> QueueEngine:
    """不起 worker、不落库（测试共享临时库，落了会污染后续用例的 restore），
    内存状态清零让 id 从 1 起步——断言才能写死具体值。"""
    eng = QueueEngine(start_workers=False)
    eng._persist = lambda: None
    with eng._lock:
        eng.state["tasks"] = []
        eng.state["seq"] = 0
    return eng


def _mark_done(eng: QueueEngine, index: int = 0) -> None:
    with eng._lock:
        eng.state["tasks"][index]["status"] = "done"
        eng.state["tasks"][index]["doneAt"] = int(time.time() * 1000)


def test_enqueue_id_matches_snapshot_and_dup_returns_minus1():
    """验收 1：enqueue_id 返回的 id 与 snapshot 里该任务的 id 一致；同链接去重两个
    入口（enqueue/enqueue_id）都返回 -1，语义不变。"""
    eng = _hermetic_engine()
    qid = eng.enqueue_id({"name": "A", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016a"})
    snap = eng.snapshot()
    assert snap["tasks"][0]["id"] == qid
    dup = {"name": "A2", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016a"}
    assert eng.enqueue_id(dup) == -1
    assert eng.enqueue(dup) == -1


def test_enqueue_pos_semantics_unchanged():
    """验收 3：enqueue() 位次语义不变——位次 = wait/run 计数，允许 ≠ 任务 id。"""
    eng = _hermetic_engine()
    assert eng.enqueue({"name": "A", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016p1"}) == 1
    assert eng.enqueue({"name": "B", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016p2"}) == 2
    _mark_done(eng, 0)  # A 完成后不计位次
    # C 位次 = 2（B、C），但 C 的真实 id 是 3——位次和 id 从此岔开，别再混用
    assert eng.enqueue({"name": "C", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016p3"}) == 2
    assert eng.enqueue_id({"name": "D", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016p4"}) == 4


def test_run_task_returns_real_id_not_pos(monkeypatch):
    """验收 2：run_task 回的 queue_task_id = 真实队列任务 id（snapshot 能对上），
    不是排队位次。"""
    tid = _make_task()
    try:
        eng = _hermetic_engine()
        # 垫一个已完成任务：没有它 id 恰好等于位次，「不等于位次」就成了空断言
        eng.enqueue({"name": "垫", "type": "quark", "path": "/", "share_url": "https://pan.quark.cn/s/t016filler"})
        _mark_done(eng, 0)
        monkeypatch.setattr(engine_mod, "get_engine", lambda: eng)

        res = pa_scheduler.run_task(tid, force=True)

        assert res["queued"] is True
        qid = res["queue_task_id"]
        mine = [x for x in eng.snapshot()["tasks"] if x.get("paTaskId") == tid]
        assert len(mine) == 1 and mine[0]["id"] == qid
        pos = sum(1 for x in eng.snapshot()["tasks"] if x["status"] in ("wait", "run"))
        assert qid != pos  # 位次是 1（仅新任务在队），真实 id 是 2
    finally:
        _drop(tid)
