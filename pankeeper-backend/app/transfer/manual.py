from __future__ import annotations

import json
import time

from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec, split_video_files
from ..adapters.factory import make_adapter
from ..db import SessionLocal
from ..models import Account, DdItem, Record
from ..services import media_push, notify, qms
from ..services.settings_svc import get_group

MAX_LOGS = 40

def _clean_folder_name(s: str) -> str:
    from ..services.names import sanitize_name

    return sanitize_name(s)

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

def run_manual(eng, t: dict, cfg: dict) -> None:
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
        exclude_names=set(t.get("excludeNames") or []),
        compare_path=t.get("comparePath") or "",
        only_paths=(set(t.get("filePaths") or []) or None),

        with_shell=bool(t.get("withShell")),

        folder_rename=_clean_folder_name((t.get("rename") or "").strip()),

        share_name=_clean_folder_name(t["name"]),
    )
    if not spec.share_url:
        _push_log(t, "ERROR", "任务缺少分享链接（shareUrl），无法转存")
        _finish(eng, t, "fail")
        return

    files: list = []
    try:
        _push_log(t, "INFO", f"解析分享链接：{t['shareUrl'][:60]}")
        files = adapter.list_share(spec)

        from .auto import dir_only_video
        only_video = t.get("onlyVideo")
        use_filter = dir_only_video(t["path"]) if only_video is None else bool(only_video)
        if use_filter:
            files, dropped = split_video_files(files)
            if dropped:
                _push_log(t, "INFO", f"过滤其他文件：跳过 {len(dropped)} 个非视频文件（只转 mkv/mp4/iso 等视频）")

        t["files"] = sum(1 for f in files if not f.is_dir)
        if not files:

            _push_log(t, "WARN", "分享内容为空（0 个文件），链接可能已失效")
            _finish(eng, t, "warn", "链接已失效（分享内容为空）")
            return
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
        _push_log(t, "ERROR", f"分享已失效：{e}")
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
        from .auto import resolve_litepan_link
        t["_media"] = {"qms_id": None, "strm_id": None}
        lp_link = resolve_litepan_link(t["path"])

        popup_event = (t.get("lpEvent") or "").strip()
        if popup_event and get_group("litepan").get("enabled"):
            lp_link = {"event": popup_event}
        if lp_link is None:
            _push_log(t, "INFO", "该目录未配置 LitePan 联动（转存配置里没开），跳过推送")
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
            "source": t.get("source", "search"),
        })
        return {"st": "未执行", "cls": "t-off"}, {"st": "未执行", "cls": "t-off"}
    if t.get("mediaOff"):

        t["_media"] = {"qms_id": None, "strm_id": None}
        qms_snap = {"st": "未执行", "cls": "t-off"}
        strm_snap = {"st": "未执行", "cls": "t-off"}
        _push_log(t, "INFO", "联动已关闭，转存完成后不触发 QMS/STRM")
        return qms_snap, strm_snap

    qms_id = t.get("qmsId")
    if qms_id is None:
        link = _match_dd_link(t["path"])
        qms_id = link.get("qms_id") if link else None
        strm_id = link.get("strm_id") if link else None
    else:

        strm_id = _strm_for_qms(qms_id)
    t["_media"] = {"qms_id": qms_id, "strm_id": strm_id}

    qms_snap = {"st": "未执行", "cls": "t-off"}
    strm_snap = {"st": "未执行", "cls": "t-off"}
    if qms_id is None:
        _push_log(t, "INFO", "该目录未配置 QMS 联动，跳过刮削")
        return qms_snap, strm_snap
    if result.add == 0:
        _push_log(t, "INFO", "没有新增文件，不触发 QMS（在库文件刮削无意义）")
        return qms_snap, strm_snap

    _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 QMS 刮削")
    t["phase"] = "qms"
    t["phaseStart"] = int(time.time() * 1000)

    from ..services import run_watch
    t["_qms_baseline"] = run_watch.record_fingerprint([e.get("name") for e in result.transferred])
    ok, msg = qms.trigger_scrape(int(qms_id))

    qms_snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-off" if ok else "t-bad"}
    _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{qms_id} 触发{'成功' if ok else '失败'}：{msg or '详见 QMS 侧日志'}")
    if strm_id is not None and ok:

        strm_snap = {"st": "等待刮削完成…", "cls": "t-off"}
        _push_log(t, "STEP", f"STRM 联动已挂后台（同步目录 #{strm_id}）：QMS 刮削成功后自动触发定向同步临时任务（成功才生成）")

    return qms_snap, strm_snap

def _strm_for_qms(qms_id: int) -> int | None:
    sid = qms.strm_id_for_qms(qms_id)
    if sid:
        return sid
    with SessionLocal() as s:
        d = s.query(DdItem).filter(DdItem.qms_id == int(qms_id), DdItem.qms_on.is_(True)).first()
        return d.strm_id if d and d.strm_id else None

def _match_dd_link(path: str) -> dict | None:
    with SessionLocal() as s:
        for d in s.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all():
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                return {"qms_id": d.qms_id, "strm_id": d.strm_id}
    return None

def _sleep_phase(eng, t: dict, phase: str, seconds: int, log_txt: str) -> None:
    _push_log(t, "STEP", log_txt)
    with eng._lock:
        t["phase"] = phase
        t["phaseStart"] = int(time.time() * 1000)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.5)

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

    rid = None
    with SessionLocal() as s:
        hist = Record(
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
            source="search",
            backend=get_group("media").get("backend", "qms"),
            logs_json=json.dumps(t["logs"], ensure_ascii=False),
            files_json=json.dumps(files_snap or [], ensure_ascii=False),
        )
        s.add(hist)
        s.commit()
        rid = hist.id

    media = t.get("_media") or {}
    qms_id = media.get("qms_id")
    strm_id = media.get("strm_id")
    qms_fired = bool(qms_snap and qms_snap.get("st") == "已触发")
    if result and result.transferred and qms_fired:
        from ..services import media_push, run_watch
    
        names = [e.get("name") for e in result.transferred]
        baseline = t.get("_qms_baseline") or {}
        strm_plan = None
        run_watch.watch_qms(rid, t["name"].split(".")[0], names, baseline=baseline, table=Record)
        if strm_id:
            delay = int(get_group("queue_cfg").get("strm", 10))
            strm_plan = {"strm_id": int(strm_id), "delay": delay}
            run_watch.trigger_strm_after_scrape(rid, int(qms_id), int(strm_id), delay, table=Record)
        media_push.watch_and_spawn({
            "drive": t["type"],
            "task": t["name"].split(".")[0],
            "names": names,
            "qms_ok": True if qms_fired else None,
            "strm_plan": strm_plan,
            "run_id": rid,
            "strm_table": "Record",
            "qms_baseline": baseline,
            "source": t.get("source", "search"),
        })
    if status == "fail":
        notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind="search_fail")
