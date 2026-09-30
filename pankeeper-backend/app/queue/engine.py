"""转存队列引擎（真实版）。

对齐前端契约（PanKeeper-vue3/src/queue/engine.ts 的状态形状与阶段机）：
- 所有转存动作只做一件事：入队；串行慢跑，阶段机 transfer→waitqms→qms→waitstrm→strm→done
- 线程数/转存间隔/QMS、STRM 延迟读 queue_cfg，改完对新任务生效
- 已完成任务保留 30 分钟后出队
- 内存是权威 + SQLite queue_tasks 快照，重启恢复（run 中断的任务回 wait，去重保证幂等）

与原型 mock 的差异：transfer 阶段跑真实 adapter，QMS/STRM 是真实 HTTP 触发（QMS 侧有自己的日志队列，
这里只记录触发结果快照）。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from ..adapters.base import AdapterError, CloudAdapter, CredentialExpired, ShareBanned, TaskSpec
from ..adapters.quark import QuarkAdapter
from ..db import SessionLocal
from ..models import Account, DdItem, QueueTaskRow, Record
from ..services import notify, qms
from ..services import media_push
from ..services.settings_svc import get_group

KEEP_DONE = 30 * 60  # 已完成任务保留 30 分钟（秒）
MAX_LOGS = 40
TICK = 0.5

ADAPTERS: dict[str, type[CloudAdapter]] = {"quark": QuarkAdapter}


def _make_adapter(drive_type: str, acc_id: int | None = None) -> CloudAdapter:
    cls = ADAPTERS.get(drive_type)
    if cls is None:
        raise AdapterError(f"网盘 {drive_type} 的适配器尚未实现（当前支持：{'/'.join(ADAPTERS)}）")
    with SessionLocal() as s:
        if acc_id is not None:
            acc = s.get(Account, acc_id)
            if acc is None or acc.type != drive_type:
                raise AdapterError("转存任务指定的账号不存在")
        else:
            acc = s.query(Account).filter(Account.type == drive_type).order_by(Account.id).first()
    # 同 deps.make_adapter_for：凭据是否配置只看 cookies_enc，不看 status
    if acc is None or not acc.cookies_enc:
        raise AdapterError(f"{drive_type} 账号未配置凭据，请先到「网盘连接」绑定")
    return cls(acc.cookies_enc)


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
                    }
                )
                self.state["seq"] = max(self.state["seq"], r.id)
            self.state["tasks"] = tasks
        self._prune(int(time.time() * 1000))

    def _prune(self, now_ms: int) -> None:
        before = len(self.state["tasks"])
        self.state["tasks"] = [
            t for t in self.state["tasks"] if not (t["status"] == "done" and t["doneAt"] and now_ms - t["doneAt"] > KEEP_DONE * 1000)
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

    # ---------- 日志/进度（transfer 阶段回调） ----------

    def _push_log(self, t: dict, lv: str, txt: str) -> None:
        t["logs"].append({"lv": lv, "txt": txt})
        if len(t["logs"]) > MAX_LOGS:
            t["logs"].pop(0)

    # ---------- 对外 API ----------

    def enqueue(self, item: dict) -> int:
        with self._lock:
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
                self._push_log(task, "ERROR", f"引擎异常：{e}")
                self._finish(task, "fail")
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
                    self._push_log(t, "STEP", "轮到它了，开始转存")
                    return t
            return None

    def _run_task(self, t: dict, cfg: dict) -> None:
        name_head = t["name"].split(".")[0]
        try:
            adapter = _make_adapter(t["type"], t.get("acc_id"))
        except AdapterError as e:
            self._push_log(t, "ERROR", str(e))
            self._finish(t, "fail")
            return

        spec = TaskSpec(share_url=t["shareUrl"], share_code=t["shareCode"], save_dir=t["path"], include_subdirs=t["includeSubdirs"])
        if not spec.share_url:
            self._push_log(t, "ERROR", "任务缺少分享链接（shareUrl），无法转存")
            self._finish(t, "fail")
            return

        try:
            self._push_log(t, "INFO", f"解析分享链接：{t['shareUrl'][:60]}")
            files = adapter.list_share(spec)
            t["files"] = len(files)
            total_size = sum(f.size for f in files)
            self._push_log(t, "INFO", f"获取分享内文件清单，共 {len(files)} 项（{total_size / 1024**3:.1f} GB）")
            self._push_log(t, "STEP", f"开始转存：{name_head} …")

            def on_progress(p: int) -> None:
                with self._lock:
                    t["progress"] = p

            def on_log(txt: str) -> None:
                self._push_log(t, "INFO", txt)

            result = adapter.save_files(files, spec, on_progress, on_log)
            self._push_log(t, "STEP", f"转存完成：新增 {result.add} / 跳过 {result.skip} / 失败 {result.fail}")
            if result.renamed:
                self._push_log(t, "INFO", f"按规则重命名 {result.renamed} 项")
        except ShareBanned as e:
            self._push_log(t, "ERROR", f"分享已失效：{e}（已熔断，后续不再请求该链接）")
            self._finish(t, "fail", f"分享失效：{e}")
            return
        except CredentialExpired as e:
            self._push_log(t, "ERROR", str(e))
            self._mark_account_expired(t["type"], t.get("acc_id"))
            self._finish(t, "fail", str(e))
            return
        except AdapterError as e:
            self._push_log(t, "ERROR", f"转存失败：{e}")
            self._finish(t, "fail", str(e))
            return

        # ---- 转存成功，走 QMS/STRM 联动（阶段机照前端契约，延迟可配） ----
        qms_snap, strm_snap = self._run_media_chain(t, cfg, result)
        self._finish(t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result)

    def _run_media_chain(self, t: dict, cfg: dict, result) -> tuple[dict, dict]:
        """触发 QMS / STRM。链接来源：转存配置里 qms_on 的目录（按目标路径前缀匹配）。"""
        link = self._match_dd_link(t["path"])
        qms_snap = {"st": "未执行", "cls": "t-off"}
        strm_snap = {"st": "未执行", "cls": "t-off"}
        if link is None:
            self._push_log(t, "INFO", "该目录未配置 QMS 联动，跳过刮削")
            return qms_snap, strm_snap
        if result.add == 0:
            self._push_log(t, "INFO", "没有新增文件，不触发 QMS（在库文件刮削无意义）")
            return qms_snap, strm_snap

        self._sleep_phase(t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 QMS 刮削")
        t["phase"] = "qms"
        t["phaseStart"] = int(time.time() * 1000)
        ok, msg = qms.trigger_scrape(link["qms_id"])
        qms_snap = {"st": "成功" if ok else f"失败 · {msg}", "cls": "t-ok" if ok else "t-bad"}
        self._push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{link['qms_id']} 触发{'成功' if ok else '失败'}：{msg or '详见 QMS 侧日志'}")

        strm_ok: bool | None = None
        if link.get("strm_id") and ok:
            self._sleep_phase(t, "waitstrm", int(cfg.get("strm", 10)), f"QMS 触发完成，{cfg.get('strm', 10)} 秒后触发 STRM 生成")
            t["phase"] = "strm"
            t["phaseStart"] = int(time.time() * 1000)
            ok2, msg2 = qms.trigger_strm(link["strm_id"])
            strm_ok = ok2
            strm_snap = {"st": "成功" if ok2 else f"失败 · {msg2}", "cls": "t-ok" if ok2 else "t-bad"}
            self._push_log(t, "INFO" if ok2 else "ERROR", f"STRM 同步 #{link['strm_id']} 触发{'成功' if ok2 else '失败'}：{msg2 or '详见 QMS 侧日志'}")

        if ok:
            # 联动推送：等 QMS 刮削完成后查记录 + TMDB 拼富文本推送（后台守护，不阻塞队列）
            media_push.watch_and_spawn({
                "drive": t["type"],
                "task": name_head,
                "names": [e["name"] for e in result.transferred],
                "qms_ok": ok,
                "strm_ok": strm_ok,
            })
        return qms_snap, strm_snap

    def _match_dd_link(self, path: str) -> dict | None:
        with SessionLocal() as s:
            for d in s.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all():
                if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                    return {"qms_id": d.qms_id, "strm_id": d.strm_id}
        return None

    def _sleep_phase(self, t: dict, phase: str, seconds: int, log_txt: str) -> None:
        self._push_log(t, "STEP", log_txt)
        with self._lock:
            t["phase"] = phase
            t["phaseStart"] = int(time.time() * 1000)
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(TICK)

    def _mark_account_expired(self, drive_type: str, acc_id: int | None = None) -> None:
        """标记凭据失效，并**只在「由好变坏」时推送一次**。

        凭据一直没更新的情况下，每次任务失败都推会变成每日骚扰——与每日探活
        保持同一套语义：推一次，之后静默，直到凭据重新验证通过。
        """
        prev: str | None = None
        display = drive_type
        with SessionLocal() as s:
            if acc_id is not None:
                acc = s.get(Account, acc_id)
            else:
                acc = s.query(Account).filter(Account.type == drive_type).order_by(Account.id).first()
            if acc:
                prev = acc.status
                display = acc.display_name
                acc.status = "expired"
                s.commit()
        if prev is not None and prev != "expired" and notify.drive_enabled(str(acc_id) if acc_id else drive_type):
            notify.push("凭据过期告警", f"{display} 凭据已失效，请到「网盘连接」重新绑定", kind="cred")

    def _finish(self, t: dict, status: str, message: str = "", qms_snap: dict | None = None, strm_snap: dict | None = None, result=None) -> None:
        now_ms = int(time.time() * 1000)
        with self._lock:
            t["status"] = status
            t["doneAt"] = now_ms
            t["phase"] = ""
            if status == "done":
                t["progress"] = 100
                self.state["lastDone"] = now_ms
            self._cond.notify_all()
        # 记录快照（含手动任务；手动任务不接推送——交互契约）
        with SessionLocal() as s:
            s.add(
                Record(
                    n=t["name"],
                    t=t["type"],
                    p=t["path"],
                    st=("完成 %d/%d" % (result.add, t["files"] or result.add)) if status == "done" and result else (message or ("已完成" if status == "done" else "失败")),
                    cls="t-ok" if status == "done" else "t-bad",
                    tm=time.strftime("%m-%d %H:%M"),
                    qms_json=json.dumps(qms_snap or {"st": "未执行", "cls": "t-off"}, ensure_ascii=False),
                    strm_json=json.dumps(strm_snap or {"st": "未执行", "cls": "t-off"}, ensure_ascii=False),
                    share_url=t.get("shareUrl", ""),
                    share_code=t.get("shareCode", ""),
                    logs_json=json.dumps(t["logs"], ensure_ascii=False),
                )
            )
            s.commit()
        if status == "fail":
            notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind="fail")

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
