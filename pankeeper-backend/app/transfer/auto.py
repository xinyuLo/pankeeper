"""自动转存流程（定时调度 pa_scheduler 入队的任务，source=auto）。

⚠️ 与手动转存 manual.py 刻意分成两个文件、流程各自完整一份（允许代码重复）：
这边独有的业务——ShareBanned 熔断回写 PaTask、完成后回写任务状态 + RunHistory、
失败推送走 auto 来源开关。改这边的逻辑不要碰 manual.py，反过来也一样。
网盘 API 层在 adapters/ 下，同样每盘独立文件（baidu/quark/pan115 互不依赖）。
"""
from __future__ import annotations

import json
import time

from ..adapters.base import AdapterError, CredentialExpired, TaskSpec
from ..adapters.factory import make_adapter
from ..db import SessionLocal
from ..models import Account, DdItem, PaTask, Record, RunHistory
from ..services import media_push, notify, qms

MAX_LOGS = 40


def _push_log(t: dict, lv: str, txt: str) -> None:
    t["logs"].append({"lv": lv, "txt": txt})
    if len(t["logs"]) > MAX_LOGS:
        t["logs"].pop(0)


def run_auto(eng, t: dict, cfg: dict) -> None:
    """自动任务全流程：解析 → 转存 → QMS/STRM 联动 → 记录 + PaTask 回写。"""
    name_head = t["name"].split(".")[0]
    try:
        # state 里的 key 是驼峰 accId（enqueue 写入），别读成 acc_id——那是永远取不到的
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
    )
    if not spec.share_url:
        _push_log(t, "ERROR", "任务缺少分享链接（shareUrl），无法转存")
        _finish(eng, t, "fail")
        return

    try:
        _push_log(t, "INFO", f"解析分享链接：{t['shareUrl'][:60]}")
        files = adapter.list_share(spec)
        t["files"] = len(files)
        if not files:
            # errno=0 但清单为空 = 典型死链。warn 收场：不推 Server 酱，记录页黄色「链接已失效」。
            _push_log(t, "WARN", "分享内容为空（0 个文件），链接可能已失效")
            _finish(eng, t, "warn", "链接已失效（分享内容为空）")
            return
        total_size = sum(f.size for f in files)
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
        _finish(eng, t, "fail", f"分享失效：{e}")
        return
    except CredentialExpired as e:
        _push_log(t, "ERROR", str(e))
        _mark_account_expired(t["type"], t.get("accId"))
        _finish(eng, t, "fail", str(e))
        return
    except AdapterError as e:
        _push_log(t, "ERROR", f"转存失败：{e}")
        _finish(eng, t, "fail", str(e))
        return

    # ---- 转存成功，走 QMS/STRM 联动（阶段机照前端契约，延迟可配） ----
    qms_snap, strm_snap = _media_chain(eng, t, cfg, result, name_head)
    _finish(eng, t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result)


def _media_chain(eng, t: dict, cfg: dict, result, name_head: str) -> tuple[dict, dict]:
    """触发 QMS / STRM。链接来源：转存配置里 qms_on 的目录（按目标路径前缀匹配）。"""
    link = _match_dd_link(t["path"])
    qms_snap = {"st": "未执行", "cls": "t-off"}
    strm_snap = {"st": "未执行", "cls": "t-off"}
    if link is None:
        _push_log(t, "INFO", "该目录未配置 QMS 联动，跳过刮削")
        return qms_snap, strm_snap
    if result.add == 0:
        _push_log(t, "INFO", "没有新增文件，不触发 QMS（在库文件刮削无意义）")
        return qms_snap, strm_snap

    _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 QMS 刮削")
    t["phase"] = "qms"
    t["phaseStart"] = int(time.time() * 1000)
    ok, msg = qms.trigger_scrape(link["qms_id"])
    qms_snap = {"st": "成功" if ok else f"失败 · {msg}", "cls": "t-ok" if ok else "t-bad"}
    _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{link['qms_id']} 触发{'成功' if ok else '失败'}：{msg or '详见 QMS 侧日志'}")

    strm_ok: bool | None = None
    if link.get("strm_id") and ok:
        _sleep_phase(eng, t, "waitstrm", int(cfg.get("strm", 10)), f"QMS 触发完成，{cfg.get('strm', 10)} 秒后触发 STRM 生成")
        t["phase"] = "strm"
        t["phaseStart"] = int(time.time() * 1000)
        ok2, msg2 = qms.trigger_strm(link["strm_id"])
        strm_ok = ok2
        strm_snap = {"st": "成功" if ok2 else f"失败 · {msg2}", "cls": "t-ok" if ok2 else "t-bad"}
        _push_log(t, "INFO" if ok2 else "ERROR", f"STRM 同步 #{link['strm_id']} 触发{'成功' if ok2 else '失败'}：{msg2 or '详见 QMS 侧日志'}")

    if ok:
        # 联动推送：等 QMS 刮削完成后查记录 + TMDB 拼富文本推送（后台守护，不阻塞队列）
        media_push.watch_and_spawn({
            "drive": t["type"],
            "task": name_head,
            "names": [e["name"] for e in result.transferred],
            "qms_ok": ok,
            "strm_ok": strm_ok,
            "source": t.get("source", "auto"),
        })
    return qms_snap, strm_snap


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


def _mark_pa_banned(t: dict, reason: str) -> None:
    """自动任务的分享死了：标记熔断，调度器看到 ban_reason 就不再入队（省风控暴露）。"""
    task_id = t.get("paTaskId")
    if not task_id:
        return
    with SessionLocal() as s:
        pa = s.get(PaTask, task_id)
        if pa and not pa.ban_reason:
            pa.ban_reason = reason[:200]
            s.commit()


def _mark_account_expired(drive_type: str, acc_id: int | None = None) -> None:
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


def _finish(eng, t: dict, status: str, message: str = "", qms_snap: dict | None = None, strm_snap: dict | None = None, result=None) -> None:
    """收尾：更新任务状态 + 记录快照 + PaTask 回写。失败推送走 auto 来源开关。"""
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
    # 记录快照
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
                logs_json=json.dumps(t["logs"], ensure_ascii=False),
            )
        )
        s.commit()
    _sync_pa_task(t, status, result)
    if status == "fail":
        notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind="auto_fail")


def _sync_pa_task(t: dict, status: str, result) -> None:
    """自动任务（带 paTaskId）完成/失败后回写任务状态与执行历史。

    add/skip 统计只有成功路径才有 TransferResult；入队即拒（缺链接/无适配器）
    的失败 result 为 None，历史里记 0 即可——失败原因已在任务日志里。
    """
    task_id = t.get("paTaskId")
    if not task_id:
        return
    add = result.add if result else 0
    skip = result.skip if result else 0
    fail = result.fail if result else 0
    with SessionLocal() as s:
        pa = s.get(PaTask, task_id)
        if pa is None:
            return
        pa.last_run = time.strftime("%m-%d %H:%M")
        pa.last_status = "success" if status == "done" else "fail"
        pa.last_result = f"新增 {add} / 跳过 {skip} / 失败 {fail}" if status == "done" else t["logs"][-1]["txt"] if t["logs"] else "失败"
        if status == "done":
            pa.ban_reason = ""
        s.add(
            RunHistory(
                task_id=task_id,
                started=time.strftime("%m-%d %H:%M"),
                finished=time.strftime("%m-%d %H:%M"),
                status="success" if status == "done" else "fail",
                add=add,
                skip=skip,
                fail=fail,
                excl=len(t.get("excludeNames") or []),
                logs_json=json.dumps(t["logs"], ensure_ascii=False),
            )
        )
        s.commit()
