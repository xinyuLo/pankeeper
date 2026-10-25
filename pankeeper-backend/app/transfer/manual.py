"""手动转存流程（搜索页「快速转存 / 转存」入队的任务，source=search）。

管道（日志/快照/台账/收尾/联动挂载）在 core.py 共用，**改管道只改 core，改完两条
链路一起生效**；业务分支（建壳 spec、onlyVideo 开关、ShareLocked 收尾、mediaOff、
显式 qmsId/strmId 优先级）保留在本文件，**改分支不用碰 auto.py**。
网盘 API 层在 adapters/ 下，每盘独立文件（baidu/quark/pan115 互不依赖）。
"""
from __future__ import annotations

import time

from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, ShareLocked, TaskSpec, split_video_files
from ..adapters.factory import make_adapter
from ..services import media_push, qms
from ..services.settings_svc import get_group
from .targets import (  # T-012：解析件收口 targets；T-013④ 内联 import 提到顶层（targets 不反向依赖 manual，无循环）
    _match_dd_link,
    _strm_for_qms,
    dir_only_video,
    resolve_litepan_link,
)
from .core import (
    files_snap as _files_snap,
    finish as core_finish,
    mark_account_expired as _mark_account_expired,
    push_log as _push_log,
    sleep_phase as _sleep_phase,
    transfer_share,
)

# 本链路的收尾参数（差异只用参数表达，见 core.finish）：手动记录 source=search、
# 只写 Record、链路挂 Record.id、失败推送走 search 来源开关（恒推送，无 enabled 闸）
_FINISH_KW = dict(record_source="search", hist_sink="record", notify_kind="search_fail")


def _clean_folder_name(s: str) -> str:
    """壳文件夹名兜底清洗（emoji/非法字符）——enqueue 已洗过，这里兜 restore 等绕道路径。"""
    from ..services.names import sanitize_name

    return sanitize_name(s)


def run_manual(eng, t: dict, cfg: dict) -> None:
    """手动任务全流程：解析 → 转存 → QMS/STRM 联动 → 记录快照。"""
    name_head = t["name"].split(".")[0]
    try:
        # state 里的 key 是驼峰 accId（enqueue 写入），别读成 acc_id——那是永远取不到的
        adapter = make_adapter(t["type"], t.get("accId"))
    except AdapterError as e:
        _push_log(t, "ERROR", str(e))
        core_finish(eng, t, "fail", **_FINISH_KW)
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
        core_finish(eng, t, "fail", **_FINISH_KW)
        return

    files: list = []
    all_files: list = []  # 台账过滤（T-002）前的清单——详情快照里台账命中项也要有去向
    try:
        _push_log(t, "INFO", f"解析分享链接：{t['shareUrl'][:60]}")
        files = adapter.list_share(spec)
        # 过滤其他文件：放在项数统计前，「完成 N/M」分母只算留下的。
        # 弹窗显式指定（onlyVideo，普通转存弹窗开关默认开）优先；没带（快速转存等）按目录配置
        only_video = t.get("onlyVideo")
        use_filter = dir_only_video(t["path"]) if only_video is None else bool(only_video)
        if use_filter:
            files, dropped = split_video_files(files)
            if dropped:
                _push_log(t, "INFO", f"过滤其他文件：跳过 {len(dropped)} 个非视频文件（只转 mkv/mp4/iso 等视频）")
        # 项数只数文件（目录条目不计）——队列行「N 项」和「完成 N/M」的分母都指它，
        # 带壳分享里壳目录也算一项的话 1 个文件会显示成「完成 1/2」
        t["files"] = sum(1 for f in files if not f.is_dir)
        if not files:
            # errno=0 但清单为空 = 典型死链（链接过期/取消分享后页面仍能打开）。
            # 按警告收场而非完成/失败：不推 Server 酱（避免死链任务天天骚扰），
            # 记录页显示黄色「链接已失效」。
            _push_log(t, "WARN", "分享内容为空（0 个文件），链接可能已失效")
            core_finish(eng, t, "warn", message="链接已失效（分享内容为空）", **_FINISH_KW)
            return
        total_size = sum(f.size for f in files if not f.is_dir)  # 目录行自带整目录合计，计入会双重计算
        _push_log(t, "INFO", f"获取分享内文件清单，共 {len(files)} 项（{total_size / 1024**3:.1f} GB）")
        _push_log(t, "STEP", f"开始转存：{name_head} …")

        def on_progress(p: int) -> None:
            with eng._lock:
                t["progress"] = p

        def on_log(txt: str) -> None:
            _push_log(t, "INFO", txt)

        all_files = list(files)  # T-011-F3：先留全量清单——save_files 阶段抛错时失败详情不退化成空表
        # T-002 本地台账 + 转存（管道在 core）：手动无任务 id，task_id 记 0
        # （唯一键含 save_dir，不同目录互不干扰）；全命中则短路 save_files——零网盘请求
        result, all_files = transfer_share(
            adapter, spec, files,
            drive=t["type"], acc_id=t.get("accId"), task_id=int(t.get("paTaskId") or 0),
            on_progress=on_progress, on_log=on_log,
            log_step=lambda txt: _push_log(t, "STEP", txt),
        )
    except ShareBanned as e:
        _push_log(t, "ERROR", f"分享已失效：{e}")
        core_finish(eng, t, "fail", message=f"分享失效：{e}", snap_rows=_files_snap(all_files, spec, None), **_FINISH_KW)
        return
    except ShareLocked as e:
        # 链接活着但要提取码/访问码：与死链空清单同口径 warn 收场、不推送（防轰炸）。
        # 正常路径已被前端预检（locked 拦截）和 AI transfer 闸挡住，漏到这里说明码不对
        # 或分享方中途加了码——重试也不可能成，推了只会骚扰。
        _push_log(t, "WARN", f"分享需要提取码/访问码：{e}")
        core_finish(eng, t, "warn", message=f"需要提取码/访问码：{e}", snap_rows=_files_snap(all_files, spec, None), **_FINISH_KW)
        return
    except CredentialExpired as e:
        _push_log(t, "ERROR", str(e))
        _mark_account_expired(t["type"], t.get("accId"))
        core_finish(eng, t, "fail", message=str(e), snap_rows=_files_snap(all_files, spec, None), **_FINISH_KW)
        return
    except AdapterError as e:
        _push_log(t, "ERROR", f"转存失败：{e}")
        core_finish(eng, t, "fail", message=str(e), snap_rows=_files_snap(all_files, spec, None), **_FINISH_KW)
        return

    # ---- 转存成功，走 QMS/STRM 联动（阶段机照前端契约，延迟可配） ----
    qms_snap, strm_snap = _media_chain(eng, t, cfg, result, name_head)
    core_finish(eng, t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result, snap_rows=_files_snap(all_files, spec, result), **_FINISH_KW)


def _media_chain(eng, t: dict, cfg: dict, result, name_head: str) -> tuple[dict, dict]:
    """触发 QMS/STRM。STRM 目标三档：**弹窗下拉显式选的**（2026-10-09 恢复）→
    转存配置目录的 strm_id → 按 QMS 整理目标自动配对（strm_id_for_qms）；
    刮削成功才生成、失败不生成。

    QMS 目标来源两档：任务显式指定的 qmsId（普通转存弹窗下拉）→ 按目标路径前缀匹配
    「转存配置」里 qms_on 的目录。解析结果挂在 t["_media"] 上——core.finish 落库后挂
    回填/STRM 线程要用同一份，别再现场 _match_dd_link（显式指定会被目录匹配覆盖掉）。"""
    if get_group("media").get("backend", "qms") != "qms":
        # 联动后端切到 LitePan：按目录配的 lp_on/lp_event 推 webhook（未配的目录跳过）
        from ..services import litepan
        t["_media"] = {"qms_id": None, "strm_id": None}
        lp_link = resolve_litepan_link(t["path"])
        # 弹窗里填了事件名（搜索转存弹窗输入框）= 一次性联动：不要求目录开过 lp_on
        # （2026-10-07 用户定稿：弹窗永远可选），只受设置页 LitePan「启用联动」总闸约束
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
        # LitePan 模式的**独立推送流程**：PanKeeper 自己识别 TMDB 直接推送（与 QMS 流程隔离）
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
    # 弹窗显式选了 STRM 优先（2026-10-09 恢复弹窗下拉）；没填维持上面的目录值/自动配对
    if t.get("strmId") is not None:
        strm_id = t.get("strmId")
    # 自动配对兜底（2026-10-13 修复）：目录匹配分支的 dd.strm_id 为空时，也按「QMS 整理目标 =
    # 同步路径」自动配对——此前只有弹窗显式选了 QMS 的分支才配对，转存配置页「不选=自动配对」
    # 的承诺在这条路上是空的，普通转存 STRM 静默不触发（NAS 爱乐之城实录）
    if qms_id is not None and strm_id is None:
        strm_id = _strm_for_qms(qms_id)
    t["_media"] = {"qms_id": qms_id, "strm_id": strm_id}
    # 反转开关（队列配置全局）：先生成 STRM 再触发 QMS。挂到 t 上，core.finish 落库后的
    # STRM 挂后台据此跳过（反转时 STRM 已经触发过，不能再等刮削）
    reverse = bool(get_group("queue_cfg").get("reverse"))
    t["_media_reverse"] = reverse

    qms_snap = {"st": "未执行", "cls": "t-off"}
    strm_snap = {"st": "未执行", "cls": "t-off"}
    if qms_id is None:
        _push_log(t, "INFO", "该目录未配置 QMS 联动，跳过刮削")
        return qms_snap, strm_snap
    if result.add == 0:
        _push_log(t, "INFO", "没有新增文件，不触发 QMS（在库文件刮削无意义）")
        return qms_snap, strm_snap

    if reverse and strm_id is not None:
        # 反转：先 STRM（扫的是转存原目录，不等刮削）、隔 strm 秒再触发 QMS
        _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 STRM 同步（反转模式：先生成 STRM）")
        t["phase"] = "strm"
        t["phaseStart"] = int(time.time() * 1000)
        ok_s, msg_s = qms.trigger_strm(int(strm_id))
        strm_snap = {"st": "正在生成 STRM（先行）" if ok_s else f"失败 · {msg_s}", "cls": "t-run" if ok_s else "t-bad"}
        _push_log(t, "INFO" if ok_s else "ERROR", f"（反转）STRM 同步 #{strm_id} 先行触发{'成功' if ok_s else '失败'}" + (f"：{msg_s}" if msg_s else ""))
        _sleep_phase(eng, t, "waitstrm", int(cfg.get("strm", 10)), f"等待 {cfg.get('strm', 10)} 秒后触发 QMS 刮削（反转模式）")
    elif reverse:
        # 校验在前端拦了，这里是执行侧兜底：没 STRM 就按默认顺序走，不把联动弄丢
        _push_log(t, "WARN", "反转模式需要 STRM 同步路径，本次未选——退回默认顺序（先 QMS 后 STRM）")

    t["phase"] = "qms"
    t["phaseStart"] = int(time.time() * 1000)
    # 触发**前**抓记录指纹：回填/推送只认本次触发产生的新记录（与 auto.py 同款，教训见 run_watch._wait）
    from ..services import run_watch
    t["_qms_baseline"] = run_watch.record_fingerprint([e.get("name") for e in result.transferred])
    ok, msg = qms.trigger_scrape(int(qms_id))
    ride = not ok and qms.rejected_by_concurrent(msg)
    if ride:
        # 同路径刮削互斥被拒（并发转存撞车，2026-10-15 银河护卫队2 实录）：正在跑/下一把
        # 刮削扫的是同一目录，会代刮本次文件——转搭车等待，不算失败（旧实现就此收工：
        # 不等刮削、STRM 不挂、推送报 ❌，其实文件后来被并发批次刮成了）
        qms_snap = {"st": "已触发（搭车：同路径刮削进行中）", "cls": "t-run"}
        _push_log(t, "INFO", f"QMS 刮削任务 #{qms_id} 触发被拒（{msg}），同路径刮削进行中——搭车等待其结果")
    else:
        # 与自动转存同款口径（2026-10-04 用户要求："qms那已经是刮削失败了，pankeeper还显示qms触发成功"）：
        # 触发受理 ≠ 刮削成功，快照先如实写「已触发」，真实结果由 run_watch 后台轮询回填
        qms_snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-run" if ok else "t-bad"}
        _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{qms_id} 触发{'成功' if ok else '失败'}"
                  + (f"：{msg}" if msg else "") + ("，等待 STRM 生成" if ok and strm_id is not None else ""))
    t["_qms_ride"] = ride
    if strm_id is not None and (ok or ride) and not t.get("_media_reverse"):
        # 默认顺序：STRM 挂后台等刮削完成再触发（搭车模式同款，等的是代刮的记录）。
        # 反转模式已在上面先触发过，不再挂
        strm_snap = {"st": "等待中", "cls": "t-wait"}

    # 推送（watch_and_spawn）挪到 core.finish 落库后：那里才有 record id，推送线程才能等 STRM 触发结果
    return qms_snap, strm_snap