"""T-006 · run_task 透出入队返回的队列任务 id（执行监控按它精确匹配本轮）。"""
from __future__ import annotations

from app.db import SessionLocal
from app.models import PaTask
from app.queue import engine as engine_mod
from app.services import pa_scheduler


class _FakeEngine:
    """snapshot 空（无并发同链接任务）；enqueue 记录入参并返回预设 id。"""

    def __init__(self, qid: int):
        self.qid = qid
        self.items: list[dict] = []

    def snapshot(self):
        return {"tasks": []}

    def enqueue(self, item: dict) -> int:
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
    """验收 2：返回体带 queue_task_id 且等于 enqueue 的返回值；入参带 pa_task_id。"""
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
    """引擎侧同链接去重（enqueue 返回 -1，snapshot 检查与入队之间的竞态窗口）：
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
