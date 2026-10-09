from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..db import SessionLocal
from ..models import QueueTaskRow
from ..services.settings_svc import get_group
from ..transfer import run_auto, run_manual

KEEP_DONE = 60 * 60
TICK = 0.5

class QueueEngine:
    def __init__(self, start_workers: bool = True) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.state: dict[str, Any] = {"seq": 0, "lastTick": 0, "lastDone": 0, "tasks": []}
        self.restore()
        if start_workers:
            for i in range(4):
                threading.Thread(target=self._worker, args=(i,), daemon=True, name=f"pkq-worker-{i}").start()

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
                row.regex_pattern = t.get("regexPattern") or ""
            s.commit()

    def restore(self) -> None:
        with SessionLocal() as s:
            rows = s.query(QueueTaskRow).order_by(QueueTaskRow.id).all()
            tasks = []
            for r in rows:
                status = "wait" if r.status == "run" else r.status
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
                        "regexPattern": getattr(r, "regex_pattern", "") or "",
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
        with self._cond:
            self._cond.wait(timeout)

    def push_log(self, t: dict, lv: str, txt: str) -> None:
        t["logs"].append({"lv": lv, "txt": txt})
        if len(t["logs"]) > 40:
            t["logs"].pop(0)

    def enqueue(self, item: dict) -> int:
        with self._lock:
            share_url = item.get("share_url") or item.get("shareUrl") or ""

            from ..services.names import sanitize_name
            clean_name = sanitize_name(item.get("name") or "")
            clean_rename = sanitize_name(item.get("rename") or "")

            if share_url:
                for t in self.state["tasks"]:
                    if t["status"] in ("wait", "run") and t.get("shareUrl") == share_url:
                        return -1
            self.state["seq"] += 1
            t = {
                "id": self.state["seq"],
                "name": clean_name or "未命名资源",
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

                "source": item.get("source") or "search",

                "accId": item.get("acc_id"),
                "paTaskId": item.get("pa_task_id"),
                "enabled": bool(item.get("enabled", True)),

                "regexPattern": (item.get("regex_pattern") or item.get("regexPattern") or ""),
                "excludeNames": list(item.get("exclude_names") or []),
                "excludeMd5s": list(item.get("exclude_md5s") or []),
                "comparePath": item.get("compare_path") or "",

                "filePaths": list(item.get("file_paths") or []),

                "rename": clean_rename,
                "withShell": bool(item.get("with_shell", False)),

                "qmsId": item.get("qms_id"),
                "mediaOff": bool(item.get("media_off", False)),

                "lpEvent": (item.get("lp_event") or item.get("lpEvent") or "").strip(),

                "onlyVideo": item.get("only_video"),
            }
            self.state["tasks"].append(t)
            pos = sum(1 for x in self.state["tasks"] if x["status"] in ("wait", "run"))
            self._cond.notify_all()
        self._persist()
        return pos

    def active_count(self) -> int:
        return sum(1 for t in self.state["tasks"] if t["status"] in ("wait", "run"))

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
            except Exception as e:
                self.push_log(task, "ERROR", f"引擎异常：{e}")
                self._fail_task(task, f"引擎异常：{e}")
            self._persist()

    def _claim(self, cfg: dict) -> dict | None:
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
        if t.get("source") == "auto":
            run_auto(self, t, cfg)
        else:
            run_manual(self, t, cfg)

    def _fail_task(self, t: dict, message: str) -> None:
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

    def state_public(self) -> dict:
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
