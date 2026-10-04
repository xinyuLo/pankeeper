"""手动转存流程（搜索页「快速转存 / 转存」入队的任务，source=search）。

⚠️ 与自动转存 auto.py 刻意分成两个文件、流程各自完整一份（允许代码重复）：
两边的业务规则本就不同——手动没有定时熔断/执行历史回写，推送策略也独立。
改这边的逻辑不要碰 auto.py，反过来也一样。
网盘 API 层在 adapters/ 下，同样每盘独立文件（baidu/quark/pan115 互不依赖）。
"""
from __future__ import annotations

import json
import time

from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
from ..adapters.factory import make_adapter
from ..db import SessionLocal
from ..models import Account, DdItem, Record
from ..services import media_push, notify, qms

MAX_LOGS = 40


def _clean_folder_name(s: str) -> str:
    """壳文件夹名兜底清洗（emoji/非法字符）——enqueue 已洗过，这里兜 restore 等绕道路径。"""
    from ..services.names import sanitize_name

    return sanitize_name(s)


def _push_log(t: dict, lv: str, txt: str) -> None:
    t["logs"].append({"lv": lv, "txt": txt})
    if len(t["logs"]) > MAX_LOGS:
        t["logs"].pop(0)


def _files_snap(files, spec, result) -> list:
    """分享内文件清单快照（记录详情「最近结果 → 详情」弹窗用）。
    done 记录里非新增的文件只有两种去向：去重跳过 / 未勾选；
    fail 记录没有逐文件结果，统一「未转存」。目录条目不进表。"""
    ok_names = {e.get("name") for e in (result.transferred if result else [])}

    def kept(rel: str) -> bool:
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
    """手动任务全流程：解析 → 转存 → QMS/STRM 联动 → 记录快照。"""
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
        # 「建壳转存」（快速转存弹窗）：按资源名/更名值在目标目录新建文件夹、剥壳转入；
        # 自动任务不带壳（默认 False）
        with_shell=bool(t.get("withShell")),
        # 壳名是网盘文件夹名：再洗一遍（入队已洗，这里兜 restore 恢复等绕过 enqueue 的路径）
        folder_rename=_clean_folder_name((t.get("rename") or "").strip()),
        # 资源名/任务名（队列任务名 = 更名值或资源名）：建壳时没填更名就用它当壳名
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
        # 项数只数文件（目录条目不计）——队列行「N 项」和「完成 N/M」的分母都指它，
        # 带壳分享里壳目录也算一项的话 1 个文件会显示成「完成 1/2」
        t["files"] = sum(1 for f in files if not f.is_dir)
        if not files:
            # errno=0 但清单为空 = 典型死链（链接过期/取消分享后页面仍能打开）。
            # 按警告收场而非完成/失败：不推 Server 酱（避免死链任务天天骚扰），
            # 记录页显示黄色「链接已失效」。
            _push_log(t, "WARN", "分享内容为空（0 个文件），链接可能已失效")
            _finish(eng, t, "warn", "链接已失效（分享内容为空）")
            return
        total_size = sum(f.size for f in files if not f.is_dir)  # 目录行自带整目录合计，计入会双重计算
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

    # ---- 转存成功，走 QMS/STRM 联动（阶段机照前端契约，延迟可配） ----
    qms_snap, strm_snap = _media_chain(eng, t, cfg, result, name_head)
    _finish(eng, t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result, files_snap=_files_snap(files, spec, result))


def _media_chain(eng, t: dict, cfg: dict, result, name_head: str) -> tuple[dict, dict]:
    """触发 QMS / STRM。STRM **不再独立配置**（2026-10-04 用户定稿：配了 QMS 就自动联动
    STRM，刮削成功才生成、失败不生成）——STRM 目标 = 与 QMS 配对的转存配置目录的 strm_id。

    QMS 目标来源两档：任务显式指定的 qmsId（普通转存弹窗下拉）→ 按目标路径前缀匹配
    「转存配置」里 qms_on 的目录。解析结果挂在 t["_media"] 上——_finish 落库后挂
    回填/STRM 线程要用同一份，别再现场 _match_dd_link（显式指定会被目录匹配覆盖掉）。"""
    from ..services.settings_svc import get_group
    if get_group("media").get("backend", "qms") != "qms":
        # 联动后端切到 LitePan：推送转存完成消息即收工，QMS/STRM 全流程跳过
        from ..services import litepan
        litepan.notify_transfer_done({
            "drive": t["type"], "task": name_head, "path": t["path"],
            "files": [{"name": e.get("name")} for e in result.transferred],
            "share_url": t.get("shareUrl", ""), "share_code": t.get("shareCode", ""),
        })
        t["_media"] = {"qms_id": None, "strm_id": None}
        _push_log(t, "INFO", "联动后端为 LitePan：转存完成消息已推送，后续整理由 LitePan 处理")
        # LitePan 模式的**独立推送流程**：自识别 TMDB 直接推送（与 QMS 流程隔离）
        media_push.watch_and_spawn({
            "drive": t["type"], "task": name_head,
            "names": [e.get("name") for e in result.transferred],
            "backend": "litepan",
            "source": t.get("source", "search"),
        })
        return {"st": "未执行", "cls": "t-off"}, {"st": "未执行", "cls": "t-off"}
    if t.get("mediaOff"):
        # 弹窗联动开关明确关掉：连目录前缀匹配都不做，如实记录
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
        # STRM 与 QMS 自动配对：转存配置里同一条目录的 strm_id（该目录 qms_on 关了 = 没配）
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
    ok, msg = qms.trigger_scrape(int(qms_id))
    # 与自动转存同款口径（2026-10-04 用户要求："qms那已经是刮削失败了，pankeeper还显示qms触发成功"）：
    # 触发受理 ≠ 刮削成功，快照先如实写「已触发」，真实结果由 run_watch 后台轮询回填
    qms_snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-off" if ok else "t-bad"}
    _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{qms_id} 触发{'成功' if ok else '失败'}：{msg or '详见 QMS 侧日志'}")
    if strm_id is not None and ok:
        # STRM 真等刮完再触发（刮削有失败不生成）：_finish 落库后挂 trigger_strm_after_scrape 后台线程
        strm_snap = {"st": "等待刮削完成…", "cls": "t-off"}
        _push_log(t, "STEP", f"STRM 联动已挂后台（同步目录 #{strm_id}）：QMS 刮削成功后自动触发定向同步临时任务（成功才生成）")

    # 推送（watch_and_spawn）挪到 _finish 落库后：那里才有 record id，推送线程才能等 STRM 结果
    return qms_snap, strm_snap


def _strm_for_qms(qms_id: int) -> int | None:
    """与 QMS 配对的 STRM 同步路径：**QMS 侧自动配对**（刮削整理目标根 ↔ 同步路径，
    2026-10-04 用户定稿：转存配置里不再单独选 STRM）；QMS 侧拿不到再回退转存配置
    里存的 strm_id（历史数据）。"""
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


def _finish(eng, t: dict, status: str, message: str = "", qms_snap: dict | None = None, strm_snap: dict | None = None, result=None, files_snap: list | None = None) -> None:
    """收尾：更新任务状态 + 写记录快照。失败推送走 search 来源的开关（notify 内部分流）。"""
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
    # 记录快照（手动任务不接富文本推送——交互契约；失败提示走 notify 内部的 search_fail 开关）
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
            source="search",  # 手动查询转存：记录页唯一来源
            logs_json=json.dumps(t["logs"], ensure_ascii=False),
            files_json=json.dumps(files_snap or [], ensure_ascii=False),
        )
        s.add(hist)
        s.commit()
        rid = hist.id
    # QMS 触发受理 ≠ 刮削成功：挂真实结果回填 + STRM 等刮完再触发（有失败不生成）+ 富文本推送
    media = t.get("_media") or {}
    qms_id = media.get("qms_id")
    strm_id = media.get("strm_id")
    qms_fired = bool(qms_snap and qms_snap.get("st") == "已触发")
    if result and result.transferred and qms_fired:
        from ..services import media_push, run_watch
        from ..services.settings_svc import get_group

        names = [e.get("name") for e in result.transferred]
        strm_plan = None
        run_watch.watch_qms(rid, t["name"].split(".")[0], names, table=Record)
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
            "source": t.get("source", "search"),
        })
    if status == "fail":
        notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind="search_fail")
