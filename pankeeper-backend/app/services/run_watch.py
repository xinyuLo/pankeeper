from __future__ import annotations

import json
import threading
import time

from ..db import SessionLocal
from ..models import PaTask, RunHistory
from ..adapters.base import AdapterError, is_video_file
from . import qms

POLL_INTERVAL = 30
POLL_TIMEOUT = 30 * 60
NO_RECORD_TIMEOUT = 10 * 60
DEDUP_WAIT = 120
SCRAPE_POLL_INTERVAL = 10
SCRAPE_WAIT_TIMEOUT = 5 * 60
CLOCK_TOLERANCE = 60

PAGE_SIZE = 500

def video_names(names) -> list[str]:
    return [n for n in (names or []) if n and is_video_file(n)]

_STRM_RESULTS: dict[int, dict] = {}

def get_strm_result(run_id: int | None, table=RunHistory) -> dict | None:
    if run_id is None:
        return None
    return _STRM_RESULTS.get(f"{table.__name__}:{run_id}")

TERMINAL_STATUS = {"renamed", "scrape_failed", "rename_failed"}
IGNORE_STATUS = {"ignore"}
FAILED_STATUS = {"scrape_failed", "rename_failed"}

def record_fingerprint(names: list[str]) -> dict[str, list]:
    want = {n for n in (names or []) if n}
    if not want:
        return {}
    out: dict[str, list] = {}
    for r in qms.scrape_records(page_size=PAGE_SIZE) or []:
        fn = r.get("file_name")
        if fn in want and fn not in out:
            out[fn] = [r.get("id"), r.get("status")]
    return out

def watch_qms(run_id: int, task_name: str, names: list[str], baseline: dict[str, list] | None = None, table=RunHistory) -> None:
    names = [n for n in (names or []) if n]
    if not run_id or not names:
        return
    threading.Thread(target=_watch, args=(run_id, task_name or "", names, baseline or {}, table), daemon=True).start()

def _scrape_dest_dirs(names: set[str]) -> list[str]:
    sps = qms.scrape_pathes() or []
    dests: dict[str, None] = {}
    for rec in qms.scrape_records(page_size=500) or []:
        if rec.get("file_name") not in names:
            continue
        src = (rec.get("path") or "").strip()
        new_path = (rec.get("new_path") or "").strip("/")
        if not new_path:
            continue
        for sp in sps:
            sp_src = (sp.get("source_path") or "").strip().rstrip("/")
            if sp_src and (src + "/").startswith(sp_src + "/"):
                root = (sp.get("dest_path") or "").strip().rstrip("/")
                if root:
                    dests.setdefault(root + "/" + new_path, None)
                break
    return list(dests)

def _fire_strm(strm_id: int, names: set[str]) -> dict:
    try:
        sp = qms.get_sync_path(strm_id)
        if not sp:
            raise AdapterError("拿不到 QMS 同步路径详情")
        if (sp.get("source_type") or "") != "openlist":
            raise AdapterError(f"同步路径类型 {sp.get('source_type')} 不支持定向（仅 openlist）")
        remote = (sp.get("remote_path") or "").strip("/")
        local = sp.get("local_path") or ""
        account_id = int(sp.get("account_id") or 0)
        if not local or not account_id or not remote:
            raise AdapterError("同步路径缺 local_path/remote_path/account_id")
        dests = [d for d in _scrape_dest_dirs(names) if (d.strip("/") + "/").startswith(remote + "/")]
        if not dests:
            raise AdapterError("刮削记录里没有落在该同步路径下的目录")
        for d in dests:
            ok, msg = qms.manual_sync("0", d, local, account_id)
            if not ok:
                raise AdapterError(f"QMS 拒绝定向同步 {d}：{msg}")
            print(f"[run-watch] STRM 定向同步：{d}（只扫本次目录）", flush=True)

        def _emby_later() -> None:
            time.sleep(90)
            ok2, msg2 = qms.refresh_emby_library()
            print(f"[run-watch] Emby 媒体库刷新（定向同步补）：{'成功' if ok2 else msg2}", flush=True)

        threading.Thread(target=_emby_later, daemon=True).start()

        return {"st": "已触发（定向临时任务）", "cls": "t-ok"}
    except Exception as e:
        print(f"[run-watch] STRM 定向同步失败（{e}），回退整路径同步", flush=True)
        ok, msg = qms.trigger_strm(strm_id)
        return {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-ok" if ok else "t-bad"}

def trigger_strm_after_scrape(
    run_id: int | None,
    qms_id: int,
    strm_id: int,
    delay: int = 10,
    timeout: int = SCRAPE_WAIT_TIMEOUT,
    table=RunHistory,
) -> None:
    def _job() -> None:
        trigger_ts = int(time.time())
        deadline = time.time() + max(1, int(timeout))
        reached = False
        degraded = False
        seen_busy = False
        while True:
            st = qms.scrape_path_status(qms_id)
            if st is None:
                degraded = True
                break
            busy = int(st.get("is_running") or 0) != 0 or bool(st.get("is_scraping"))
            updated = int(st.get("updated_at") or 0)
            if busy:
                seen_busy = True
            elif seen_busy or updated >= trigger_ts - CLOCK_TOLERANCE:
                reached = True
                break
            if time.time() > deadline:
                break
            time.sleep(SCRAPE_POLL_INTERVAL)

        if not reached and not degraded:
            snap = {"st": "未确认（刮削超时，未触发 STRM）", "cls": "t-off"}
            if run_id:
                _write_strm(run_id, snap, table)
                _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap
            print(f"[run-watch] QMS 刮削 #{qms_id} 等待超时（{timeout}s），不触发 STRM", flush=True)
            return
        names: list[str] = []
        if degraded:
            print(f"[run-watch] 取不到 QMS 刮削状态，退化为等 {delay}s 后直接触发 STRM", flush=True)
        else:

            names = _record_names(run_id, table)
            if names:
                want = set(names)
                statuses: dict[str, str] = {}
                for r in qms.scrape_records(page_size=PAGE_SIZE) or []:
                    fn = r.get("file_name")
                    if fn in want and fn not in statuses and r.get("status") not in IGNORE_STATUS:
                        statuses[fn] = r.get("status")
                failed = [n for n, s_ in statuses.items() if s_ in FAILED_STATUS]
                if failed:
                    snap = {"st": f"QMS 刮削失败 {len(failed)} 项，未生成 STRM", "cls": "t-off"}
                    if run_id:
                        _write_strm(run_id, snap, table)
                        _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap
                    print(f"[run-watch] QMS 刮削有失败（{len(failed)}/{len(statuses)}），不触发 STRM #{strm_id}", flush=True)
                    return

        time.sleep(max(0, int(delay)))

        names_set = set(names) if names else set(_record_names(run_id, table))
        if names_set:            snap = _fire_strm(strm_id, names_set)
        else:
            ok, msg = qms.trigger_strm(strm_id)

            snap = {"st": "已触发（整路径同步）" if ok else f"失败 · {msg}", "cls": "t-ok" if ok else "t-bad"}
        if run_id:
            _write_strm(run_id, snap, table)
            _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap
        print(f"[run-watch] STRM 联动（同步目录 #{strm_id}）：{snap['st']}", flush=True)

    threading.Thread(target=_job, daemon=True).start()

def _record_names(run_id: int, table=RunHistory) -> list[str]:
    if not run_id:
        return []
    with SessionLocal() as s:
        r = s.get(table, run_id)
        if r is None:
            return []
        if table is RunHistory:
            return [n for n in json.loads(r.transferred_json or "[]") if n]
        files = json.loads(getattr(r, "files_json", "") or "[]")
        return [e.get("name") for e in files if isinstance(e, dict) and e.get("name")]

def _write_strm(run_id: int, snap: dict, table=RunHistory) -> None:
    with SessionLocal() as s:
        r = s.get(table, run_id)
        if r is None:
            return
        r.strm_json = json.dumps(snap, ensure_ascii=False)
        s.commit()

def _load_snap(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None

def _watch(run_id: int, task_name: str, names: list[str], baseline: dict[str, list], table=RunHistory) -> None:
    try:
        recs, verdict = _wait(names, baseline)
        if verdict == "dedup":

            print(f"[run-watch] record {run_id} QMS 未产生新记录（按文件去重，未重刮）→ 保持原判定", flush=True)
            return
        _write(run_id, _summary(recs, names), table)
    except Exception as e:
        print(f"[run-watch] 回填 QMS 结果失败（record {run_id}）：{e}", flush=True)

def _fresh_map(latest: dict[str, dict], baseline: dict[str, list]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fn, r in latest.items():
        base = baseline.get(fn)
        if base is None:
            out[fn] = r
        elif r.get("id") is not None and base[0] is not None and r.get("id") > base[0]:
            out[fn] = r
        elif len(base) > 1 and base[1] is not None and r.get("status") != base[1]:
            out[fn] = r
    return out

def _wait(names: list[str], baseline: dict[str, list] | None = None) -> tuple[list[dict], str]:
    baseline = baseline or {}
    want = set(video_names(names))
    if not want:
        return [], "done"
    deadline = time.time() + POLL_TIMEOUT
    stale_deadline = time.time() + NO_RECORD_TIMEOUT
    dedup_deadline = time.time() + DEDUP_WAIT
    seen_max_id = 0
    initial_max_id: int | None = None
    while True:
        rows = qms.scrape_records(page_size=PAGE_SIZE) or []
        max_id = max((int(r.get("id") or 0) for r in rows), default=0)
        if initial_max_id is None:
            initial_max_id = max_id
        if max_id > seen_max_id:
            seen_max_id = max_id
            stale_deadline = time.time() + NO_RECORD_TIMEOUT
        latest: dict[str, dict] = {}
        for r in rows:

            fn = r.get("file_name")
            if fn in want and fn not in latest:
                latest[fn] = r
        fresh = _fresh_map(latest, baseline)
        alive = [r for r in fresh.values() if r.get("status") not in IGNORE_STATUS]
        missing = want - set(fresh)
        now = time.time()
        if not missing and (not alive or all(r.get("status") in TERMINAL_STATUS for r in alive)):
            return list(fresh.values()), "done"
        if (
            baseline
            and not fresh
            and all(fn in baseline for fn in want)
            and now > dedup_deadline
            and seen_max_id == initial_max_id
        ):
            return [], "dedup"
        if now > stale_deadline:
            return list(fresh.values()), ("stale" if missing else "done")
        if now > deadline:
            return list(fresh.values()), "timeout"
        time.sleep(POLL_INTERVAL)

def _summary(recs: list[dict], names: list[str]) -> dict:
    ok = sum(1 for r in recs if r.get("status") == "renamed")
    fail = sum(1 for r in recs if r.get("status") in FAILED_STATUS)

    missing = len(video_names(names)) - len({r.get("file_name") for r in recs})
    if not recs:
        return {"st": "未见刮削记录", "cls": "t-off"}
    if fail:
        st = f"成功 {ok} / 失败 {fail}" if ok else f"失败 {fail} 项（详见 QMS）"
        return {"st": st, "cls": "t-bad"}
    if missing:
        return {"st": f"成功 {ok} / 未见记录 {missing}", "cls": "t-off"}
    return {"st": "成功", "cls": "t-ok"}

def overall_of(status: str, qms_snap: dict | None) -> dict:
    if status != "success":
        return {"st": "失败", "cls": "t-bad"}
    if (qms_snap or {}).get("cls") == "t-bad":
        return {"st": "部分失败", "cls": "t-warn"}
    return {"st": "成功", "cls": "t-ok"}

def _write(run_id: int, snap: dict, table=RunHistory) -> None:
    with SessionLocal() as s:
        r = s.get(table, run_id)
        if r is None:
            return
        r.qms_json = json.dumps(snap, ensure_ascii=False)
        if table is not RunHistory:
            s.commit()
            print(f"[run-watch] record {run_id} QMS 结果回填：{snap.get('st')}", flush=True)
            return
        latest = (
            s.query(RunHistory)
            .filter(RunHistory.task_id == r.task_id)
            .order_by(RunHistory.id.desc())
            .first()
        )
        if latest is not None and latest.id == r.id:
            pa = s.get(PaTask, r.task_id)
            if pa is not None:
                ov = overall_of(r.status, snap)
                pa.last_status = {"成功": "success", "部分失败": "partial"}.get(ov["st"], "fail")
        s.commit()
    print(f"[run-watch] run {run_id} QMS 结果回填：{snap.get('st')}", flush=True)
