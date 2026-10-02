"""转存队列引擎（骨架层）——只管排队，不管转存怎么做。

职责边界（2026-10-01 拆分，别再往回塞）：
- 本文件：入队 / 状态 / 持久化恢复 / worker 线程调度 / 分发
- 业务流程：app/transfer/manual.py（搜索页手动）与 auto.py（定时任务），
  两条路径各自完整、刻意不共享代码——推送/回写/熔断规则不同
- 网盘 API：app/adapters/ 下每盘一个独立文件（baidu/quark/pan115），构造入口 factory.py

对齐前端契约（PanKeeper-vue3/src/queue/engine.ts 的状态形状与阶段机）：
- 所有转存动作只做一件事：入队；串行慢跑，阶段机 transfer→waitqms→qms→waitstrm→strm→done
- 线程数/转存间隔/QMS、STRM 延迟读 queue_cfg，改完对新任务生效
- 已完成任务保留 1 小时后出队（历史在「转存记录」）
- 内存是权威 + SQLite queue_tasks 快照，重启恢复（run 中断的任务回 wait，去重保证幂等）
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..db import SessionLocal
from ..models import QueueTaskRow
from ..services.settings_svc import get_group
from ..transfer import run_auto, run_manual

KEEP_DONE = 60 * 60  # 已完成任务保留 1 小时（秒），之后出队——历史去「转存记录」查
TICK = 0.5


class QueueEngine:
    def __init__(self, start_workers: bool = True) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.state: dict[str, Any] = {"seq": 0, "lastTick": 0, "lastDone": 0, "tasks": []}
        self.restore()
        if start_workers:
            for i in range(4):  # 线程上限 4，worker 按 cfg.threads 决定是否干活
                threading.Thread(target=self._worker, args=(i,), daemon=True, name=f"pkq-worker-{i}").start()

    # ---------- 状态与持久化 ----------

    def _persist(self) -> None:
        with SessionLocal() as s:
            for t in self.state["tasks"]:
                row = s.get(QueueTaskRow, t["id"])
                if row is None:
                    row = QueueTaskRow(id=t["id"], name=t["name"], type=t["type"])
                    s.add(row)
                row.path = t["path"]
                row.files = t["files"]
                row.size = t["size"]
                row.status = t["status"]
                row.phase = t["phase"]
                row.phase_start = t["phaseStart"]
                row.progress = t["progress"]
                row.done_at = t["doneAt"]
                row.logs_json = json.dumps(t["logs"], ensure_ascii=False)
                row.share_url = t.get("shareUrl", "")
                row.share_code = t.get("shareCode", "")
                row.include_subdirs = t.get("includeSubdirs", True)
                row.acc_id = t.get("accId")
                row.pa_task_id = t.get("paTaskId")
                row.exclude_json = json.dumps(t.get("excludeNames") or [], ensure_ascii=False)
                row.exclude_md5_json = json.dumps(t.get("excludeMd5s") or [], ensure_ascii=False)
            s.commit()

    def restore(self) -> None:
        with SessionLocal() as s:
            rows = s.query(QueueTaskRow).order_by(QueueTaskRow.id).all()
            tasks = []
            for r in rows:
                status = "wait" if r.status == "run" else r.status  # 中断的 run 回 wait（转存去重保证幂等）
                tasks.append(
                    {
                        "id": r.id,
                        "name": r.name,
                        "type": r.type,
                        "path": r.path,
                        "files": r.files,
                        "size": r.size,
                        "status": status,
                        "phase": "" if status == "wait" else r.phase,
                        "phaseStart": 0,
                        "progress": 0 if status != "done" else r.progress,
                        "doneAt": r.done_at,
                        "logs": json.loads(r.logs_json or "[]"),
                        "shareUrl": r.share_url,
                        "shareCode": r.share_code,
                        "includeSubdirs": r.include_subdirs,
                        "accId": r.acc_id,
                        "paTaskId": r.pa_task_id,
                        "excludeNames": json.loads(r.exclude_json or "[]"),
                        "excludeMd5s": json.loads(getattr(r, "exclude_md5_json", "") or "[]"),
                    }
                )
                self.state["seq"] = max(self.state["seq"], r.id)
            self.state["tasks"] = tasks
        self._prune(int(time.time() * 1000))

    def _prune(self, now_ms: int) -> None:
        before = len(self.state["tasks"])
        self.state["tasks"] = [
            t for t in self.state["tasks"] if not (t["status"] in ("done", "warn", "fail") and t["doneAt"] and now_ms - t["doneAt"] > KEEP_DONE * 1000)
        ]
        if len(self.state["tasks"]) != before:
            with SessionLocal() as s:
                keep_ids = {t["id"] for t in self.state["tasks"]}
                for row in s.query(QueueTaskRow).all():
                    if row.id not in keep_ids:
                        s.delete(row)
                s.commit()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, ensure_ascii=False))

    def wait_change(self, timeout: float = 2.0) -> None:
        """SSE 用：状态变化或超时返回。"""
        with self._cond:
            self._cond.wait(timeout)

    # ---------- 日志（转存流程层回调用） ----------

    def push_log(self, t: dict, lv: str, txt: str) -> None:
        t["logs"].append({"lv": lv, "txt": txt})
        if len(t["logs"]) > 40:
            t["logs"].pop(0)

    # ---------- 对外 API ----------

    def enqueue(self, item: dict) -> int:
        with self._lock:
            share_url = item.get("share_url") or item.get("shareUrl") or ""
            # 同链接去重：wait/run 中已有同一 shareUrl 的任务就不再入队（手动快速
            # 转存连点两次 = 两个任务各打一遍百度全链，纯浪费请求喂风控）。
            # 返回 -1 由前端提示「已在队列中」。
            if share_url:
                for t in self.state["tasks"]:
                    if t["status"] in ("wait", "run") and t.get("shareUrl") == share_url:
                        return -1
            self.state["seq"] += 1
            t = {
                "id": self.state["seq"],
                "name": item.get("name") or "未命名资源",
                "type": item.get("type") or "quark",
                "path": item.get("path") or "/",
                "files": int(item.get("files") or 0),
                "size": item.get("size") or "—",
                "status": "wait",
                "phase": "",
                "phaseStart": 0,
                "progress": 0,
                "doneAt": 0,
                "logs": [],
                "shareUrl": item.get("share_url") or item.get("shareUrl") or "",
                "shareCode": item.get("share_code") or item.get("shareCode") or "",
                "includeSubdirs": bool(item.get("include_subdirs", True)),
                # 任务来源：search=搜索转存（默认）/ auto=自动转存（定时调度入队时带）。
                # 决定走 manual.py 还是 auto.py 流程（推送/回写/熔断规则不同）
                "source": item.get("source") or "search",
                # 自动任务链路：指定账号 / 来源 PaTask / 排除清单
                "accId": item.get("acc_id"),
                "paTaskId": item.get("pa_task_id"),
                "excludeNames": list(item.get("exclude_names") or []),
                "excludeMd5s": list(item.get("exclude_md5s") or []),
                "comparePath": item.get("compare_path") or "",
                # 勾选清单（搜索页分享树勾选；空=全部）。注意：不持久化，
                # 重启恢复的任务勾选丢失回全量——有 MD5/名字去重兜底，宁可多查不少删
                "filePaths": list(item.get("file_paths") or []),
            }
            self.state["tasks"].append(t)
            pos = sum(1 for x in self.state["tasks"] if x["status"] in ("wait", "run"))
            self._cond.notify_all()
        self._persist()
        return pos

    def active_count(self) -> int:
        return sum(1 for t in self.state["tasks"] if t["status"] in ("wait", "run"))

    # ---------- worker ----------

    def _worker(self, index: int) -> None:
        while True:
            cfg = get_group("queue_cfg")
            if index >= int(cfg.get("threads", 1)):
                time.sleep(1.0)
                continue
            task = self._claim(cfg)
            if task is None:
                time.sleep(TICK)
                continue
            try:
                self._run_task(task, cfg)
            except Exception as e:  # 兜底：任何异常不让 worker 死掉
                self.push_log(task, "ERROR", f"引擎异常：{e}")
                self._fail_task(task, f"引擎异常：{e}")
            self._persist()

    def _claim(self, cfg: dict) -> dict | None:
        """提一个 wait 任务上场：空闲线程已保证（本 worker 就是线程位），还需距上次完成 >= gap。"""
        with self._lock:
            now_ms = int(time.time() * 1000)
            if self.state["tasks"] and now_ms - (self.state["lastDone"] or 0) < int(cfg.get("gap", 5)) * 1000:
                return None
            for t in self.state["tasks"]:
                if t["status"] == "wait":
                    t["status"] = "run"
                    t["phase"] = "transfer"
                    t["phaseStart"] = now_ms
                    self.push_log(t, "STEP", "轮到它了，开始转存")
                    return t
            return None

    def _run_task(self, t: dict, cfg: dict) -> None:
        """按来源分发到对应流程文件——本层不做任何转存业务判断。"""
        if t.get("source") == "auto":
            run_auto(self, t, cfg)
        else:
            run_manual(self, t, cfg)

    def _fail_task(self, t: dict, message: str) -> None:
        """worker 兜底用最简收尾：只改状态，业务层异常时不应走到这里。"""
        now_ms = int(time.time() * 1000)
        with self._lock:
            t["status"] = "fail"
            t["doneAt"] = now_ms
            t["phase"] = ""
            self._cond.notify_all()
        from ..models import Record

        with SessionLocal() as s:
            s.add(
                Record(
                    n=t["name"],
                    t=t["type"],
                    p=t["path"],
                    st=message or "失败",
                    cls="t-bad",
                    tm=time.strftime("%m-%d %H:%M"),
                    qms_json=json.dumps({"st": "未执行", "cls": "t-off"}, ensure_ascii=False),
                    strm_json=json.dumps({"st": "未执行", "cls": "t-off"}, ensure_ascii=False),
                    share_url=t.get("shareUrl", ""),
                    share_code=t.get("shareCode", ""),
                    logs_json=json.dumps(t["logs"], ensure_ascii=False),
                )
            )
            s.commit()

    # ---------- 便捷入口（API 层用） ----------

    def state_public(self) -> dict:
        """对前端的完整状态（与 queueView 同形）。"""
        snap = self.snapshot()
        now_ms = int(time.time() * 1000)
        self._prune(now_ms)
        return snap


engine: QueueEngine | None = None


def get_engine() -> QueueEngine:
    global engine
    if engine is None:
        engine = QueueEngine()
    return engine
