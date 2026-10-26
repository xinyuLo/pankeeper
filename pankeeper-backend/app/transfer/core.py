"""转存管道共享内核（T-011 / D-1 方案 B：管道共用、业务分支分家）。

auto.py（定时任务）/ manual.py（搜索转存）两条链路里**必须一致**的管道件收拢在这里：
日志、清单快照、阶段等待、凭据失效标记、台账预过滤+转存、收尾落库与后台链路挂载。
**改管道只改 core，改完两条链路一起生效**；业务分支（建壳、正则、排除、联动目标
解析、落库模型）各自保留在 auto.py / manual.py，**改分支不用碰对方**。

finish 的差异只用参数表达（不嗅探来源）：
- record_source：写进 Record.source（auto="auto" / manual="search"）
- hist_sink："pa"（Record → RunHistory → touch_records，后台链路挂 RunHistory.id，
  STRM 目标由 strm_link_resolver 现场解析，watch_qms 带 on_state 回填 RunHistory——
  默认表，不带 table）/"record"（Record → touch_records，链路挂 Record.id，STRM
  目标取 t["_media"]，watch_qms 带 on_state + table=Record 回填 Record）
- notify_kind：失败推送分流（auto_fail / search_fail）
- enabled_gate：auto 侧的 t["enabled"] 判断（调用点求值后传入）；manual 恒真
- run_history_writer：hist_sink="pa" 的协作者——auto._sync_pa_task（瘦身版，
  只写 PaTask + RunHistory，返回 run_id；无 paTaskId/PaTask 行不在时返回 None，
  RunHistory 与后台链路一并跳过，与既有行为一致）

⚠️ 时序钉死：auto 是 Record → RunHistory → touch_records()；manual 是 Record →
touch_records()。touch_records() 的位置决定前端行出现时机，合并后必须与改前一致。
⚠️ 日志文案是数据不是壳：db.py 的一次性回填按 logs_json LIKE '%LitePan%' 判定
旧记录走过 LitePan——**别改任何日志文案/结构/级别**。
⚠️ queue/engine.py 里有第三份同款日志上限（硬编码 40），core 不 import engine
（会循环导入）——改阈值要三处一起看。
"""
from __future__ import annotations

import json
import time

from ..adapters.base import TransferResult
from ..db import SessionLocal
from ..models import Account, Record
from ..services import media_push, notify, run_watch
from ..services.settings_svc import get_group
from .ledger import ledger_prefilter, ledger_write

MAX_LOGS = 40


def push_log(t: dict, lv: str, txt: str) -> None:
    t["logs"].append({"lv": lv, "txt": txt})
    if len(t["logs"]) > MAX_LOGS:
        t["logs"].pop(0)


def files_snap(files, spec, result) -> list[dict]:
    """分享内文件清单快照（记录详情「最近结果 → 详情」弹窗用）。
    done 记录里非新增的文件只有两种去向：去重跳过 / 未勾选；
    fail 记录没有逐文件结果，统一「未转存」。目录条目不进表。"""
    ok_names = {e.get("name") for e in (result.transferred if result else [])}

    def kept(rel: str) -> bool:
        rel = rel.strip("/")  # 两侧同口径剥斜杠（2026-10-08 修复勾选全不中）
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


def sleep_phase(eng, t: dict, phase: str, seconds: int, log_txt: str) -> None:
    push_log(t, "STEP", log_txt)
    with eng._lock:
        t["phase"] = phase
        t["phaseStart"] = int(time.time() * 1000)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.5)


def mark_account_expired(drive_type: str, acc_id: int | None = None) -> None:
    """标记凭据失效，并**只在「由好变坏」时推送一次**（每日探活同语义，防每日骚扰）。"""
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


def transfer_share(
    adapter,
    spec,
    files: list,
    *,
    drive: str,
    acc_id,
    task_id: int,
    on_progress,
    on_log,
    log_step,
):
    """转存 + 本地台账（T-002 起两条链路逐字相同的管道段）。

    台账预过滤 → 全命中短路（TransferResult(skip=N, skip_ledger=N, ledger_skipped=名单)，
    零网盘请求）→ adapter.save_files → 回写台账并累加 skip（skip_ledger/ledger_skipped
    同步落账，T-017 跳过三路拆账）。返回 (result, all_files)：all_files = 台账过滤前的
    完整清单（详情快照里台账命中项也要有去向）。
    log_step：STEP 级日志入口（push_log(t, "STEP", …)）——STEP 是既有日志数据结构，
    不能借道 on_log（那边是 INFO 级）。"""
    all_files = files
    files, ledger_hits = ledger_prefilter(drive, task_id, spec.save_dir, files, on_log=on_log)
    if ledger_hits and not any(not f.is_dir for f in files):
        # 全命中：不再调用适配器（连网盘基线都不查），直接收工；名单照样带出（T-017）
        result = TransferResult()
        result.skip = len(ledger_hits)
        result.skip_ledger = len(ledger_hits)
        result.ledger_skipped = list(ledger_hits)
        log_step(f"转存完成：新增 0 / 跳过 {result.skip} / 失败 0（本地台账全命中，未请求网盘）")
    else:
        result = adapter.save_files(files, spec, on_progress, on_log)
        if ledger_hits:
            result.skip += len(ledger_hits)
            result.skip_ledger = len(ledger_hits)
            result.ledger_skipped = list(ledger_hits)
        # 转存成功落账（防 QMS 把文件搬进媒体库后重复转存）；写失败不影响转存
        ledger_write(drive, acc_id, task_id, spec.share_url, spec.save_dir, files, result)
        log_step(f"转存完成：新增 {result.add} / 跳过 {result.skip} / 失败 {result.fail}")
    if result.renamed:
        on_log(f"按规则重命名 {result.renamed} 项")
    return result, all_files


def finish(
    eng,
    t: dict,
    status: str,
    *,
    message: str = "",
    qms_snap: dict | None = None,
    strm_snap: dict | None = None,
    result=None,
    snap_rows: list | None = None,
    record_source: str,
    hist_sink: str,
    notify_kind: str,
    enabled_gate: bool = True,
    run_history_writer=None,
    strm_link_resolver=None,
) -> None:
    """收尾管道：状态机 → 落 Record →（pa：RunHistory）→ touch_records → 后台链路
    （watch_qms / trigger_strm_after_scrape / media_push.watch_and_spawn）→ 失败推送。

    时序钉死（见模块头）：touch_records() 的位置决定前端行出现时机。"""
    now_ms = int(time.time() * 1000)
    with eng._lock:
        t["status"] = status
        t["doneAt"] = now_ms
        t["phase"] = ""
        if status in ("done", "warn"):
            # warn（死链）同样算走完一轮：进度拉满、重置转存间隔，下一任务照常排队
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
            source=record_source,
            backend=get_group("media").get("backend", "qms"),
            logs_json=json.dumps(t["logs"], ensure_ascii=False),
            files_json=json.dumps(snap_rows or [], ensure_ascii=False),
        )
        s.add(hist)
        s.commit()
        rid = hist.id
    if hist_sink == "pa":
        # auto：Record → RunHistory → touch_records；无 paTaskId / PaTask 行不在时
        # RunHistory 与链路一并跳过（与既有行为一致）
        run_id = run_history_writer(t, status, result, qms_snap, strm_snap) if run_history_writer else None
        if run_id is None:
            if status == "fail" and enabled_gate:
                notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind=notify_kind)
            return
        # T-015：把 RunHistory.id 回写到刚落的那条 Record——后台终态回填（run_watch）
        # 顺着这个关联把同一份快照同步写进 Record；此前只写 RunHistory，记录页的
        # qms/strm 停在收尾初始快照（2026-10-26 老大现场发现 STRM 一直「等待中」）
        with SessionLocal() as s:
            rec = s.get(Record, rid)
            if rec is not None:
                rec.run_history_id = run_id
                s.commit()
        run_watch.touch_records()
        qms_fired = bool(qms_snap and str(qms_snap.get("st", "")).startswith("已触发"))
        # 触发过但被拒（快照「失败 ·」开头）也推——转存成功联动挂了，手机必须知道（2026-10-13 清单四）
        qms_attempted = bool(qms_snap and str(qms_snap.get("st", "")).startswith("失败"))
        if result and result.transferred and (qms_fired or qms_attempted):
            names = [e.get("name") for e in result.transferred]
            baseline = t.get("_qms_baseline") or {}
            strm_plan = None
            if qms_fired:
                run_watch.watch_qms(run_id, t["name"].split(".")[0], names, baseline=baseline,
                                    on_state=lambda st: run_watch.write_qms_interim(run_id, st))
                link = strm_link_resolver(t) if strm_link_resolver else None
                if link and link.get("strm_id") and not t.get("_media_reverse"):
                    # 反转模式 STRM 已先行触发过，不再挂「等刮削→触发 STRM」；
                    # 没配 STRM 时整段不挂
                    delay = int(get_group("queue_cfg").get("strm", 10))
                    strm_plan = {"strm_id": int(link["strm_id"]), "delay": delay}
                    run_watch.trigger_strm_after_scrape(run_id, int(link["qms_id"]), int(link["strm_id"]), delay,
                                                        ride=bool(t.get("_qms_ride")),
                                                        log_fn=lambda txt: push_log(t, "STEP", txt))
            media_push.watch_and_spawn({
                "drive": t["type"],
                "task": t["name"].split(".")[0],
                "names": names,
                "qms_ok": qms_fired,
                "strm_plan": strm_plan,
                "run_id": run_id,
                "qms_baseline": baseline,
                "source": t.get("source", "auto"),
            })
    else:
        # manual：Record → touch_records；链路挂 Record.id，STRM 目标取 t["_media"]
        run_watch.touch_records()
        media = t.get("_media") or {}
        qms_id = media.get("qms_id")
        strm_id = media.get("strm_id")
        # 「已触发」前缀匹配兼容搭车快照「已触发（搭车：同路径刮削进行中）」——搭车也要挂
        qms_fired = bool(qms_snap and str(qms_snap.get("st", "")).startswith("已触发"))
        qms_attempted = bool(qms_snap and str(qms_snap.get("st", "")).startswith("失败"))
        if result and result.transferred and (qms_fired or qms_attempted):
            names = [e.get("name") for e in result.transferred]
            baseline = t.get("_qms_baseline") or {}
            strm_plan = None
            if qms_fired:
                run_watch.watch_qms(rid, t["name"].split(".")[0], names, baseline=baseline, table=Record,
                                    on_state=lambda st: run_watch.write_qms_interim(rid, st, Record))
                if strm_id and not t.get("_media_reverse"):
                    # 反转模式 STRM 已先行触发过（run_watch 会回填 QMS 真实结果，但不再挂 STRM 线程）；
                    # 没配 STRM 时整段不挂
                    delay = int(get_group("queue_cfg").get("strm", 10))
                    strm_plan = {"strm_id": int(strm_id), "delay": delay}
                    run_watch.trigger_strm_after_scrape(rid, int(qms_id), int(strm_id), delay, table=Record,
                                                        ride=bool(t.get("_qms_ride")),
                                                        log_fn=lambda txt: push_log(t, "STEP", txt))
            media_push.watch_and_spawn({
                "drive": t["type"],
                "task": t["name"].split(".")[0],
                "names": names,
                "qms_ok": qms_fired,
                "strm_plan": strm_plan,
                "run_id": rid,
                "strm_table": "Record",
                "qms_baseline": baseline,
                "source": t.get("source", "search"),
            })
    if status == "fail" and enabled_gate:
        notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind=notify_kind)
