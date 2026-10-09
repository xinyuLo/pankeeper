from __future__ import annotations

import json
import re
import time

from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec, split_video_files
from ..adapters.factory import make_adapter
from ..db import SessionLocal
from ..models import Account, DdItem, PaTask, Record, RunHistory
from ..services import media_push, notify, qms
from ..services.settings_svc import get_group
from ..services.share_cache import build_payload, share_key, share_list_cache

MAX_LOGS = 40

def _apply_regex(files: list, pat: str) -> tuple[list, int, list[str]]:
    rex = re.compile(pat)
    before = sum(1 for f in files if not f.is_dir)
    kept = [f for f in files if f.is_dir or rex.search(f.name)]
    miss = before - sum(1 for f in kept if not f.is_dir)
    return kept, miss, [f.name for f in kept if not f.is_dir]

def _apply_exclusion(files: list, names: set, md5s: set) -> tuple[list, list[str]]:
    excluded = [f.name for f in files if not f.is_dir and (f.name in names or (f.md5 and f.md5 in md5s))]
    kept = [f for f in files if f.name not in names and not (not f.is_dir and f.md5 and f.md5 in md5s)]
    return kept, excluded

def _push_log(t: dict, lv: str, txt: str) -> None:
    t["logs"].append({"lv": lv, "txt": txt})
    if len(t["logs"]) > MAX_LOGS:
        t["logs"].pop(0)

def _files_snap(files, spec, result) -> list:
    ok_names = {e.get("name") for e in (result.transferred if result else [])}

    def kept(rel: str) -> bool:
        rel = rel.strip("/")
        for sel in spec.only_paths or set():
            sel = sel.strip("/")
            if sel and (rel == sel or rel.startswith(sel + "/")):
                return True
        return False

    out = []
    for f in files:
        if f.is_dir:
            continue
        if result is not None and (f.name in ok_names or (f.target_name or f.name) in ok_names):
            st = "已转存"
        elif result is not None and spec.only_paths and not kept(f.path):
            st = "未勾选"
        elif result is not None:
            st = "已在库跳过"
        else:
            st = "未转存"
        out.append({"path": f.path, "name": f.name, "size": f.size, "st": st})
    return out

def run_auto(eng, t: dict, cfg: dict) -> None:
    name_head = t["name"].split(".")[0]
    try:

        adapter = make_adapter(t["type"], t.get("accId"))
    except AdapterError as e:
        _push_log(t, "ERROR", str(e))
        _finish(eng, t, "fail")
        return

    spec = TaskSpec(
        share_url=t["shareUrl"],
        share_code=t["shareCode"],
        save_dir=t["path"],
        include_subdirs=t["includeSubdirs"],

        compare_path=t.get("comparePath") or "",
        only_paths=(set(t.get("filePaths") or []) or None),
    )
    if not spec.share_url:
        _push_log(t, "ERROR", "任务缺少分享链接（shareUrl），无法转存")
        _finish(eng, t, "fail")
        return

    files: list = []
    regex_miss = 0
    excluded_names: list[str] = []
    started_ts = time.time()

    t["_runStats"] = {"started_ts": started_ts, "regex_miss": 0, "excluded_names": []}
    try:
        _push_log(t, "INFO", f"解析分享链接：{t['shareUrl'][:60]}")
        files = adapter.list_share(spec)

        t["files"] = sum(1 for f in files if not f.is_dir)

        try:
            share_list_cache.put(share_key(t["type"], t["shareUrl"], t["shareCode"]), build_payload(files))
        except Exception:
            pass
        if not files:

            _push_log(t, "WARN", "分享内容为空（0 个文件），链接可能已失效")
            _finish(eng, t, "warn", "链接已失效（分享内容为空）")
            return

        pat = (t.get("regexPattern") or "").strip()
        if pat and t["files"]:
            try:
                files, regex_miss, regex_hit = _apply_regex(files, pat)
                t["_runStats"]["regex_miss"] = regex_miss

                t["_runStats"]["regex_hit"] = regex_hit
                _push_log(t, "INFO", f"正则过滤：命中 {len(regex_hit)} / 未命中 {regex_miss}")
            except re.error as e:
                _push_log(t, "WARN", f"正则表达式无效（{e}），本次不做过滤")

        files, excluded_names = _apply_exclusion(files, set(t.get("excludeNames") or []), set(t.get("excludeMd5s") or []))
        if excluded_names:
            t["_runStats"]["excluded_names"] = excluded_names
            _push_log(t, "INFO", f"排除清单：跳过 {len(excluded_names)} 个文件")

        if dir_only_video(t["path"]):
            files, dropped = split_video_files(files)
            if dropped:
                t["_runStats"]["excluded_names"] = (t["_runStats"]["excluded_names"] or []) + dropped
                _push_log(t, "INFO", f"过滤其他文件：剔除 {len(dropped)} 个非视频文件（只转 mkv/mp4/iso 等视频）")

        total_size = sum(f.size for f in files if not f.is_dir)
        _push_log(t, "INFO", f"获取分享内文件清单，共 {len(files)} 项（{total_size / 1024**3:.1f} GB）")
        _push_log(t, "STEP", f"开始转存：{name_head} …")

        def on_progress(p: int) -> None:
            with eng._lock:
                t["progress"] = p

        def on_log(txt: str) -> None:
            _push_log(t, "INFO", txt)

        result = adapter.save_files(files, spec, on_progress, on_log)
        _push_log(t, "STEP", f"转存完成：新增 {result.add} / 跳过 {result.skip} / 失败 {result.fail}")
        if result.renamed:
            _push_log(t, "INFO", f"按规则重命名 {result.renamed} 项")
    except ShareBanned as e:
        _push_log(t, "ERROR", f"分享已失效：{e}（已熔断，调度器不再入队该链接）")
        _mark_pa_banned(t, str(e))
        _finish(eng, t, "fail", f"分享失效：{e}", files_snap=_files_snap(files, spec, None))
        return
    except CredentialExpired as e:
        _push_log(t, "ERROR", str(e))
        _mark_account_expired(t["type"], t.get("accId"))
        _finish(eng, t, "fail", str(e), files_snap=_files_snap(files, spec, None))
        return
    except AdapterError as e:
        _push_log(t, "ERROR", f"转存失败：{e}")
        _finish(eng, t, "fail", str(e), files_snap=_files_snap(files, spec, None))
        return

    qms_snap, strm_snap = _media_chain(eng, t, cfg, result, name_head)
    _finish(eng, t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result, files_snap=_files_snap(files, spec, result))

def _media_chain(eng, t: dict, cfg: dict, result, name_head: str) -> tuple[dict, dict]:
    if get_group("media").get("backend", "qms") != "qms":

        from ..services import litepan
        lp_link = resolve_litepan_link(t["path"], t.get("paTaskId"), require_lp_on=False)
        if lp_link is None:
            _push_log(t, "INFO", "未配置 LitePan 事件名（任务弹窗/转存配置目录都没填），跳过推送")
        else:
            lp_res = litepan.notify_transfer_done({
                "drive": t["type"], "task": name_head, "path": t["path"],
                "files": [{"name": e.get("name")} for e in result.transferred],
                "share_url": t.get("shareUrl", ""), "share_code": t.get("shareCode", ""),
                "event": lp_link["event"],
            })
            _push_log(t, "INFO" if lp_res["ok"] else "WARN", f"联动后端为 LitePan：{lp_res['message']}")
        _push_log(t, "STEP", "LitePan 模式独立推送流程已挂后台（自识别 TMDB，不查 LitePan 状态）")

        media_push.watch_and_spawn({
            "drive": t["type"], "task": name_head,
            "names": [e.get("name") for e in result.transferred],
            "backend": "litepan",
            "source": t.get("source", "auto"),
        })
        return {"st": "未执行", "cls": "t-off"}, {"st": "未执行", "cls": "t-off"}
    link = resolve_media_link(t["path"], t.get("paTaskId"))
    qms_snap = {"st": "未配置", "cls": "t-off"}
    strm_snap = {"st": "未配置", "cls": "t-off"}
    if link is None:
        _push_log(t, "INFO", "该目录未配置 QMS 联动，跳过刮削")
        return qms_snap, strm_snap
    if result.add == 0:

        qms_snap = {"st": "未执行（无新增）", "cls": "t-off"}
        strm_snap = {"st": "未执行（无新增）", "cls": "t-off"}
        _push_log(t, "INFO", "没有新增文件，不触发 QMS（在库文件刮削无意义）")
        return qms_snap, strm_snap

    _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 QMS 刮削")
    t["phase"] = "qms"
    t["phaseStart"] = int(time.time() * 1000)

    from ..services import run_watch
    t["_qms_baseline"] = run_watch.record_fingerprint([e.get("name") for e in result.transferred])
    ok, msg = qms.trigger_scrape(link["qms_id"])

    qms_snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-off" if ok else "t-bad"}
    _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{link['qms_id']} 触发{'成功' if ok else '失败'}：{msg or '详见 QMS 侧日志'}")

    if link.get("strm_id"):
        strm_snap = {"st": "等待刮削完成…", "cls": "t-off"}
        _push_log(t, "STEP", f"STRM 联动已挂后台（同步目录 #{link['strm_id']}）：QMS 刮削成功后自动触发定向同步临时任务（成功才生成）")

    return qms_snap, strm_snap

def _match_dd_link(path: str) -> dict | None:
    with SessionLocal() as s:
        for d in s.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all():
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                return {"qms_id": d.qms_id, "strm_id": d.strm_id}
    return None

def _hit_dd_dir(path: str) -> dict | None:
    if not path:
        return None
    hit = None
    with SessionLocal() as s:
        for d in s.query(DdItem).all():
            base = (d.path or "").rstrip("/")
            if not base:
                continue
            if path == base or path.startswith(base + "/"):
                if hit is None or len(base) > len(hit["path"]):
                    hit = {
                        "path": base, "qms_on": bool(d.qms_on), "qms_id": d.qms_id,
                        "strm_id": d.strm_id, "only_video": bool(d.only_video),
                    }
    return hit

def dir_only_video(path: str) -> bool:
    hit = _hit_dd_dir(path or "")
    return bool(hit and hit["only_video"])

def resolve_media_link(path: str, task_id: int | None = None) -> dict | None:
    qms_id: int | None = None
    strm_id: int | None = None
    if task_id:
        with SessionLocal() as s:
            row = s.get(PaTask, task_id)
            if row is not None:
                qms_id, strm_id = row.qms_id, row.strm_id
    hit = _hit_dd_dir(path or "")
    if hit is not None and not hit["qms_on"]:
        return None
    dq = hit["qms_id"] if hit else None
    ds = hit["strm_id"] if hit else None
    rq = qms_id or dq
    if not rq:
        return None
    rs = strm_id if strm_id is not None else ds
    if rs is None:

        from .qms import strm_id_for_qms
        rs = strm_id_for_qms(rq)
    return {"qms_id": rq, "strm_id": rs}

def resolve_litepan_link(path: str, task_id: int | None = None, require_lp_on: bool = True) -> dict | None:
    if not get_group("litepan").get("enabled"):
        return None
    hit: DdItem | None = None
    with SessionLocal() as s:
        query = s.query(DdItem)
        if require_lp_on:
            query = query.filter(DdItem.lp_on.is_(True))
        for d in query.all():
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                if hit is None or len(d.path) > len(hit.path):
                    hit = d
    task_event = ""
    if task_id:
        with SessionLocal() as s:
            row = s.get(PaTask, task_id)
            if row is not None:
                task_event = (row.lp_event or "").strip()
    dir_event = (hit.lp_event or "").strip() if hit else ""
    event = task_event or dir_event
    if not event:
        return None
    return {"event": event}

def _sleep_phase(eng, t: dict, phase: str, seconds: int, log_txt: str) -> None:
    _push_log(t, "STEP", log_txt)
    with eng._lock:
        t["phase"] = phase
        t["phaseStart"] = int(time.time() * 1000)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.5)

def _mark_pa_banned(t: dict, reason: str) -> None:
    task_id = t.get("paTaskId")
    if not task_id:
        return
    with SessionLocal() as s:
        pa = s.get(PaTask, task_id)
        if pa and not pa.ban_reason:
            pa.ban_reason = reason[:200]
            s.commit()

def _mark_account_expired(drive_type: str, acc_id: int | None = None) -> None:
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

def _finish(eng, t: dict, status: str, message: str = "", qms_snap: dict | None = None, strm_snap: dict | None = None, result=None, files_snap: list | None = None) -> None:
    now_ms = int(time.time() * 1000)
    with eng._lock:
        t["status"] = status
        t["doneAt"] = now_ms
        t["phase"] = ""
        if status in ("done", "warn"):

            t["progress"] = 100
            eng.state["lastDone"] = now_ms
        eng._cond.notify_all()

    with SessionLocal() as s:
        s.add(
            Record(
                n=t["name"],
                t=t["type"],
                p=t["path"],
                st=("完成 %d/%d" % (result.add, t["files"] or result.add)) if status == "done" and result else (message or ("已完成" if status == "done" else "失败")),
                cls="t-ok" if status == "done" else ("t-warn" if status == "warn" else "t-bad"),
                tm=time.strftime("%m-%d %H:%M"),
                qms_json=json.dumps(qms_snap or {"st": "未执行", "cls": "t-off"}, ensure_ascii=False),
                strm_json=json.dumps(strm_snap or {"st": "未执行", "cls": "t-off"}, ensure_ascii=False),
                share_url=t.get("shareUrl", ""),
                share_code=t.get("shareCode", ""),
                source="auto",
                backend=get_group("media").get("backend", "qms"),
                logs_json=json.dumps(t["logs"], ensure_ascii=False),
                files_json=json.dumps(files_snap or [], ensure_ascii=False),
            )
        )
        s.commit()
    _sync_pa_task(t, status, result, qms_snap, strm_snap)
    if status == "fail" and t.get("enabled", True):

        notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind="auto_fail")

def _sync_pa_task(t: dict, status: str, result, qms_snap: dict | None = None, strm_snap: dict | None = None) -> None:
    task_id = t.get("paTaskId")
    if not task_id:
        return
    add = result.add if result else 0
    skip = result.skip if result else 0
    fail = result.fail if result else 0
    stats = t.get("_runStats") or {}
    started_ts = stats.get("started_ts")
    finished_ts = time.time()
    started_txt = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_ts)) if started_ts else time.strftime("%Y-%m-%d %H:%M")
    )
    finished_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished_ts))
    duration = int(finished_ts - started_ts) if started_ts else 0
    message = (
        "转存成功" if status == "done" else (t["logs"][-1]["txt"] if t["logs"] else "失败")
    )
    with SessionLocal() as s:
        pa = s.get(PaTask, task_id)
        if pa is None:
            return
        pa.last_run = time.strftime("%m-%d %H:%M")
        pa.last_status = "success" if status == "done" else "fail"
        pa.last_result = f"新增 {add} / 跳过 {skip} / 失败 {fail}" if status == "done" else t["logs"][-1]["txt"] if t["logs"] else "失败"
        if status == "done":
            pa.ban_reason = ""

        hist = RunHistory(
            task_id=task_id,
            started=started_txt,
            finished=finished_txt,
            status="success" if status == "done" else "fail",
            add=add,
            skip=skip,
            skip_md5=result.skip_md5 if result else 0,
            fail=fail,
            excl=len(t.get("excludeNames") or []),
            total_share=t.get("files") or 0,
            regex_miss=stats.get("regex_miss", 0),
            message=message,
            transferred_json=json.dumps([e.get("name") for e in (result.transferred if result else [])], ensure_ascii=False),
            excluded_json=json.dumps(stats.get("excluded_names", []), ensure_ascii=False),
            regex_hit_json=json.dumps(stats.get("regex_hit", []), ensure_ascii=False),

            md5_skipped_json=json.dumps(
                [e.get("name") for e in (result.md5_skipped if result else []) if e.get("name")],
                ensure_ascii=False,
            ),

            qms_json=json.dumps(qms_snap, ensure_ascii=False) if qms_snap else "",
            strm_json=json.dumps(strm_snap, ensure_ascii=False) if strm_snap else "",
            duration=duration,
            logs_json=json.dumps(t["logs"], ensure_ascii=False),
            )
        s.add(hist)
        s.commit()
        run_id_new = hist.id

    if qms_snap and qms_snap.get("st") == "已触发" and result and result.transferred:
        from ..services import media_push, run_watch

        names = [e.get("name") for e in result.transferred]
        baseline = t.get("_qms_baseline") or {}
        run_watch.watch_qms(
            run_id_new,
            t["name"].split(".")[0],
            names,
            baseline=baseline,
        )
        link = resolve_media_link(t["path"], t.get("paTaskId"))
        strm_plan = None
        if link and link.get("strm_id"):
            delay = int(get_group("queue_cfg").get("strm", 10))
            strm_plan = {"strm_id": int(link["strm_id"]), "delay": delay}
            run_watch.trigger_strm_after_scrape(run_id_new, int(link["qms_id"]), int(link["strm_id"]), delay)

        media_push.watch_and_spawn({
            "drive": t["type"],
            "task": t["name"].split(".")[0],
            "names": names,
            "qms_ok": True,
            "strm_plan": strm_plan,
            "run_id": run_id_new,
            "qms_baseline": baseline,
            "source": t.get("source", "auto"),
        })
