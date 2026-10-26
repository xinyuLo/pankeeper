"""自动转存流程（定时调度 pa_scheduler 入队的任务，source=auto）。

管道（日志/快照/台账/收尾/联动挂载）在 core.py 共用，**改管道只改 core，改完两条
链路一起生效**；业务分支（正则过滤、排除清单、分享清单缓存、任务状态/执行历史
回写、QMS/LitePan 联动目标解析）保留在本文件，**改分支不用碰 manual.py**。
网盘 API 层在 adapters/ 下，每盘独立文件（baidu/quark/pan115 互不依赖）。
"""
from __future__ import annotations

import json
import re
import time

from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec, split_video_files
from ..adapters.factory import make_adapter
from ..db import SessionLocal
from ..models import PaTask, RunHistory
from ..services import media_push, qms
from ..services.settings_svc import get_group
from ..services.share_cache import build_payload, share_key, share_list_cache
from .targets import dir_only_video, resolve_litepan_link, resolve_media_link  # T-012：目标解析收口（api 层经本模块再导出，旧引用不破）
from .core import (
    files_snap as _files_snap,
    finish as core_finish,
    mark_account_expired as _mark_account_expired,
    push_log as _push_log,
    sleep_phase as _sleep_phase,
    transfer_share,
)


def _apply_regex(files: list, pat: str) -> tuple[list, int, list[str]]:
    """正则过滤（bdsavePro 语义）：只留文件名匹配的文件，目录条目保留以维持结构。

    返回 (过滤后清单, 未命中数, 命中文件名清单)。pat 非法由调用方兜 re.error。"""
    rex = re.compile(pat)
    before = sum(1 for f in files if not f.is_dir)
    kept = [f for f in files if f.is_dir or rex.search(f.name)]
    miss = before - sum(1 for f in kept if not f.is_dir)
    return kept, miss, [f.name for f in kept if not f.is_dir]


def _apply_exclusion(files: list, names: set, md5s: set) -> tuple[list, list[str]]:
    """排除清单：文件名或 MD5 任一命中即剔除（目录只按名字——整目录排除）。

    返回 (剩余清单, 被排除的文件名)。"""
    excluded = [f.name for f in files if not f.is_dir and (f.name in names or (f.md5 and f.md5 in md5s))]
    kept = [f for f in files if f.name not in names and not (not f.is_dir and f.md5 and f.md5 in md5s)]
    return kept, excluded


def run_auto(eng, t: dict, cfg: dict) -> None:
    """自动任务全流程：解析 → 转存 → QMS/STRM 联动 → 记录 + PaTask 回写。"""
    name_head = t["name"].split(".")[0]
    try:
        # state 里的 key 是驼峰 accId（enqueue 写入），别读成 acc_id——那是永远取不到的
        adapter = make_adapter(t["type"], t.get("accId"))
    except AdapterError as e:
        _push_log(t, "ERROR", str(e))
        core_finish(eng, t, "fail", **_finish_kw(t))
        return

    spec = TaskSpec(
        share_url=t["shareUrl"],
        share_code=t["shareCode"],
        save_dir=t["path"],
        include_subdirs=t["includeSubdirs"],
        # 排除清单不传给适配器——由本流程统一过滤并如实记录（excluded 名单要落库）
        compare_path=t.get("comparePath") or "",
        only_paths=(set(t.get("filePaths") or []) or None),
    )
    if not spec.share_url:
        _push_log(t, "ERROR", "任务缺少分享链接（shareUrl），无法转存")
        core_finish(eng, t, "fail", **_finish_kw(t))
        return

    files: list = []
    all_files: list = []  # 台账过滤（T-002）前的清单——详情快照里台账命中项也要有去向
    regex_miss = 0
    excluded_names: list[str] = []
    started_ts = time.time()
    # 转存日志统计（RunHistory 写入用）：挂在任务 state 上，_sync_pa_task 收
    t["_runStats"] = {"started_ts": started_ts, "regex_miss": 0, "excluded_names": []}
    try:
        _push_log(t, "INFO", f"解析分享链接：{t['shareUrl'][:60]}")
        files = adapter.list_share(spec)
        # 项数只数文件（目录条目不计）——「完成 N/M」分母口径，壳目录也算会把 1 个文件算成 1/2
        t["files"] = sum(1 for f in files if not f.is_dir)
        # 分享清单缓存（独立于目录缓存）：转存每跑一次就刷新一次，查看/排除弹窗在两次运行之间命中缓存
        try:
            share_list_cache.put(share_key(t["type"], t["shareUrl"], t["shareCode"]), build_payload(files))
        except Exception:  # noqa: BLE001 —— 缓存写失败不影响转存
            pass
        if not files:
            # errno=0 但清单为空 = 典型死链。warn 收场：不推 Server 酱，记录页黄色「链接已失效」。
            _push_log(t, "WARN", "分享内容为空（0 个文件），链接可能已失效")
            core_finish(eng, t, "warn", message="链接已失效（分享内容为空）", **_finish_kw(t))
            return

        # 正则过滤（bdsavePro 语义）：只转存文件名匹配的文件；目录条目保留以维持结构
        pat = (t.get("regexPattern") or "").strip()
        if pat and t["files"]:
            try:
                files, regex_miss, regex_hit = _apply_regex(files, pat)
                t["_runStats"]["regex_miss"] = regex_miss
                # 正则命中清单（过滤后放行的文件名，落库供详情弹窗展示）
                t["_runStats"]["regex_hit"] = regex_hit
                _push_log(t, "INFO", f"正则过滤：命中 {len(regex_hit)} / 未命中 {regex_miss}")
            except re.error as e:
                _push_log(t, "WARN", f"正则表达式无效（{e}），本次不做过滤")

        # 排除清单：文件名或 MD5 任一命中即剔除；被剔除名单落库（RunHistory.excluded_json）
        files, excluded_names = _apply_exclusion(files, set(t.get("excludeNames") or []), set(t.get("excludeMd5s") or []))
        if excluded_names:
            t["_runStats"]["excluded_names"] = excluded_names
            _push_log(t, "INFO", f"排除清单：跳过 {len(excluded_names)} 个文件")

        # 目录开了「过滤其他文件」：非视频文件转存环节直接剔除（QMS 只整理视频，杂件转过去也是垃圾）
        if dir_only_video(t["path"]):
            files, dropped = split_video_files(files)
            if dropped:
                t["_runStats"]["excluded_names"] = (t["_runStats"]["excluded_names"] or []) + dropped
                _push_log(t, "INFO", f"过滤其他文件：剔除 {len(dropped)} 个非视频文件（只转 mkv/mp4/iso 等视频）")

        total_size = sum(f.size for f in files if not f.is_dir)  # 目录行自带整目录合计，计入会双重计算
        _push_log(t, "INFO", f"获取分享内文件清单，共 {len(files)} 项（{total_size / 1024**3:.1f} GB）")
        _push_log(t, "STEP", f"开始转存：{name_head} …")

        def on_progress(p: int) -> None:
            with eng._lock:
                t["progress"] = p

        def on_log(txt: str) -> None:
            _push_log(t, "INFO", txt)

        all_files = list(files)  # T-011-F3：先留全量清单——save_files 阶段抛错时失败详情不退化成空表
        # T-002 本地台账 + 转存（管道在 core）：命中直接跳过；全命中则短路 save_files
        # ——连网盘基线都不查（零网盘请求）
        result, all_files = transfer_share(
            adapter, spec, files,
            drive=t["type"], acc_id=t.get("accId"), task_id=int(t.get("paTaskId") or 0),
            on_progress=on_progress, on_log=on_log,
            log_step=lambda txt: _push_log(t, "STEP", txt),
        )
        # T-017：无新增时把三路去重的账摊开——「跳过 18 / MD5 17」的差值（台账/同名）
        # 不再靠猜；不管 QMS 有没有配，这行都打，收尾口径统一
        if result.add == 0 and result.skip:
            _push_log(
                t, "INFO",
                f"无新增文件：{result.skip} 项全部已在库（台账 {result.skip_ledger} + MD5 {result.skip_md5} + 同名 {len(result.name_skipped)}），本次为重复运行",
            )
    except ShareBanned as e:
        _push_log(t, "ERROR", f"分享已失效：{e}（已熔断，调度器不再入队该链接）")
        _mark_pa_banned(t, str(e))
        core_finish(eng, t, "fail", message=f"分享失效：{e}", snap_rows=_files_snap(all_files, spec, None), **_finish_kw(t))
        return
    except CredentialExpired as e:
        _push_log(t, "ERROR", str(e))
        _mark_account_expired(t["type"], t.get("accId"))
        core_finish(eng, t, "fail", message=str(e), snap_rows=_files_snap(all_files, spec, None), **_finish_kw(t))
        return
    except AdapterError as e:
        _push_log(t, "ERROR", f"转存失败：{e}")
        core_finish(eng, t, "fail", message=str(e), snap_rows=_files_snap(all_files, spec, None), **_finish_kw(t))
        return

    # ---- 转存成功，走 QMS/STRM 联动（阶段机照前端契约，延迟可配） ----
    qms_snap, strm_snap = _media_chain(eng, t, cfg, result, name_head)
    core_finish(eng, t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result, snap_rows=_files_snap(all_files, spec, result), **_finish_kw(t))


def _media_chain(eng, t: dict, cfg: dict, result, name_head: str) -> tuple[dict, dict]:
    """触发 QMS / STRM。链接来源：转存配置里 qms_on 的目录（按目标路径前缀匹配）。"""
    if get_group("media").get("backend", "qms") != "qms":
        # 联动后端切到 LitePan：按目录配的 lp_on/lp_event 推 webhook（未配的目录跳过）
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
        # LitePan 模式的**独立推送流程**（与 QMS 流程隔离）：PanKeeper 自识别 TMDB 直接推送，
        # 不等不查 LitePan 的刮削状态——它的状态对外不可见
        media_push.watch_and_spawn({
            "drive": t["type"], "task": name_head,
            "names": [e.get("name") for e in result.transferred],
            "backend": "litepan",
            "source": t.get("source", "auto"),
        })
        return {"st": "未执行", "cls": "t-off"}, {"st": "未执行", "cls": "t-off"}
    link = resolve_media_link(t["path"], t.get("paTaskId"))
    # 反转开关（队列配置全局）：先生成 STRM 再触发 QMS。挂 t 上，_sync_pa_task 落库后
    # 据此跳过「等刮削→触发 STRM」的后台线程（反转时 STRM 已经触发过）
    reverse = bool(get_group("queue_cfg").get("reverse"))
    t["_media_reverse"] = reverse
    qms_snap = {"st": "未配置", "cls": "t-off"}
    strm_snap = {"st": "未配置", "cls": "t-off"}
    if link is None:
        _push_log(t, "INFO", "该目录未配置 QMS 联动，跳过刮削")
        return qms_snap, strm_snap
    if result.add == 0:
        # 有联动配置但本次没有新增文件：不触发（在库文件刮削无意义），如实标注
        qms_snap = {"st": "未执行（无新增）", "cls": "t-off"}
        strm_snap = {"st": "未执行（无新增）", "cls": "t-off"}
        _push_log(t, "INFO", "没有新增文件，不触发 QMS（在库文件刮削无意义）")
        return qms_snap, strm_snap

    strm_id = link.get("strm_id")
    if reverse and strm_id is not None:
        # 反转：先 STRM（扫的是转存原目录，不等刮削）、隔 strm 秒再触发 QMS
        _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 STRM 同步（反转模式：先生成 STRM）")
        t["phase"] = "strm"
        t["phaseStart"] = int(time.time() * 1000)
        ok_s, msg_s = qms.trigger_strm(int(strm_id))
        strm_snap = {"st": "正在生成 STRM（先行）" if ok_s else f"失败 · {msg_s}", "cls": "t-run" if ok_s else "t-bad"}
        _push_log(t, "INFO" if ok_s else "ERROR", f"（反转）STRM 同步 #{strm_id} 先行触发{'成功' if ok_s else '失败'}" + (f"：{msg_s}" if msg_s else ""))
        _sleep_phase(eng, t, "waitstrm", int(cfg.get("strm", 10)), f"等待 {cfg.get('strm', 10)} 秒后触发 QMS 刮削（反转模式）")
    else:
        if reverse:
            # 校验在前端拦了，这里是执行侧兜底：没 STRM 就按默认顺序走，不把联动弄丢
            _push_log(t, "WARN", "反转模式需要 STRM 同步路径，该目录没选——退回默认顺序（先 QMS 后 STRM）")
        _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 QMS 刮削")
    t["phase"] = "qms"
    t["phaseStart"] = int(time.time() * 1000)
    # 触发**前**抓记录指纹：回填/推送只认本次触发产生的新记录（去重不重刮不冒充、
    # 同名旧记录不串台，见 run_watch._wait 的教训清单）
    from ..services import run_watch
    t["_qms_baseline"] = run_watch.record_fingerprint([e.get("name") for e in result.transferred])
    ok, msg = qms.trigger_scrape(link["qms_id"])
    ride = not ok and qms.rejected_by_concurrent(msg)
    if ride:
        # 同路径刮削互斥被拒（并发转存撞车，2026-10-15 银河护卫队2 实录）：正在跑/下一把
        # 刮削扫的是同一目录，会代刮本次文件——转搭车等待，不算失败
        qms_snap = {"st": "已触发（搭车：同路径刮削进行中）", "cls": "t-run"}
        _push_log(t, "INFO", f"QMS 刮削任务 #{link['qms_id']} 触发被拒（{msg}），同路径刮削进行中——搭车等待其结果")
    else:
        # ⚠️ 这里只能证明"QMS 受理了触发请求"，不是刮削结果——快照如实写「已触发」，
        # 真实结果由 run_watch 后台轮询回填（2026-10-04 用户实锤：QMS 侧 scrape_failed
        # 而这边显示"成功"）。别改回"成功"，那是骗人。
        qms_snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-run" if ok else "t-bad"}
        _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{link['qms_id']} 触发{'成功' if ok else '失败'}"
                  + (f"：{msg}" if msg else "") + ("，等待 STRM 生成" if ok and link.get("strm_id") else ""))
    t["_qms_ride"] = ride

    # STRM 不在这里等、也不在这里触发：正确做法是「等 QMS 刮削真跑完再触发」
    # （run_watch.trigger_strm_after_scrape：轮询 /api/scrape/pathes/{id} 到 is_running=0
    #  && is_scraping=false && updated_at>=触发时刻）。放这里会**堵住队列 worker 好几分钟**，
    # 所以改为收尾落库后由 core.finish 挂后台线程（2026-10-04 用户要求对齐 bdsavepro 语义）。
    if link.get("strm_id") and (ok or t.get("_qms_ride")) and not reverse:
        # 搭车模式同挂「等刮削→触发 STRM」：等的是代刮产生的记录（2026-10-15）
        strm_snap = {"st": "等待中", "cls": "t-wait"}

    # 推送（watch_and_spawn）挪到 core.finish 落库后：那里才有 run_id，推送线程才能
    # 等 STRM 触发结果、信息条如实显示「STRM 已生成」（2026-10-04 用户要求）
    return qms_snap, strm_snap


# 目录级兜底见 targets._match_dd_link（最长前缀匹配）——旧版「库序首个命中」实现
# 全仓无调用方，T-013③ 删除，别按它再写一份。


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


def _resolve_strm_link(t: dict) -> dict | None:
    """core.finish(hist_sink="pa") 的 STRM 目标解析回调：现场解析（与既有链路同款）。"""
    return resolve_media_link(t["path"], t.get("paTaskId"))


def _sync_pa_task(t: dict, status: str, result, qms_snap: dict | None = None, strm_snap: dict | None = None) -> int | None:
    """自动任务（带 paTaskId）完成/失败后回写任务状态与执行历史。

    T-011 起瘦身成 core.finish(hist_sink="pa") 的 run_history_writer：只写 PaTask +
    RunHistory 并**返回 run_id**；无 paTaskId / PaTask 行不在返回 None（RunHistory 与
    后台链路一并跳过——core 按返回值分流）。链路挂载（touch_records/回填/STRM/推送）
    在 core.finish，别在这里加回来。

    add/skip 统计只有成功路径才有 TransferResult；入队即拒（缺链接/无适配器）
    的失败 result 为 None，历史里记 0 即可——失败原因已在任务日志里。
    转存日志统计（分享数/正则未命中/MD5 跳过/转存与排除文件名/起止秒级时间）
    从 t["_runStats"] 收——run_auto 里边跑边填。
    """
    task_id = t.get("paTaskId")
    if not task_id:
        return None
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
    ledger_skipped = result.ledger_skipped if result else []
    name_skipped = result.name_skipped if result else []
    if status == "done":
        # T-017/F2：说明按 add/fail 分流——有新增讲新增；零新增但有失败说失败
        # （别把「失败 N 个」说成重复执行）；真·全在库才报重复执行（三路摊开）
        message = (
            f"转存成功 · 新增 {add}"
            if add > 0
            else (
                f"完成：新增 0 / 失败 {fail}"
                if fail > 0
                else f"台账 {len(ledger_skipped)} + MD5 过滤 {result.skip_md5 if result else 0} + 同名 {len(name_skipped)}，重复执行（无新增）"
            )
        )
    else:
        message = t["logs"][-1]["txt"] if t["logs"] else "失败"
    with SessionLocal() as s:
        pa = s.get(PaTask, task_id)
        if pa is None:
            return None
        pa.last_run = time.strftime("%m-%d %H:%M")
        pa.last_status = "success" if status == "done" else "fail"
        pa.last_result = f"新增 {add} / 跳过 {skip} / 失败 {fail}" if status == "done" else t["logs"][-1]["txt"] if t["logs"] else "失败"
        if status == "done":
            pa.ban_reason = ""
        # ⚠️ 此处曾实现「MD5 去重跳过的文件自动回写任务排除清单（名字+MD5）」。
        # 2026-10-04 用户拍板取消：那等于任务静默改自己的配置——删掉本地文件想重转时
        # 会被清单挡住，而改动不看日志根本发现不了（实锤案例：40.4k.mp4 被自动勾进排除清单）。
        # 现在只在日志 / RunHistory 如实记录（skip_md5 计数 + 适配器的「MD5 命中跳过」日志行），
        # 要排除由用户在「排除文件清单」弹窗里手动勾选（走 POST /pa/tasks/{id}/exclude）。
        # 不要恢复这段回写，除非用户再次明确要求。
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
            # MD5 去重命中的文件名：只记录供详情查看（"哪集被 MD5 滤掉"），不回写任务配置
            md5_skipped_json=json.dumps(
                [e.get("name") for e in (result.md5_skipped if result else []) if e.get("name")],
                ensure_ascii=False,
            ),
            # T-017：台账 / 同名命中名单（详情弹窗两块明细）。台账先于 MD5/名字闸；
            # 同名 md5_diff=True 内容可能不同只标黄。只记录，不回写任务配置
            ledger_skipped_json=json.dumps(ledger_skipped, ensure_ascii=False),
            name_skipped_json=json.dumps(name_skipped, ensure_ascii=False),
            # QMS/STRM 联动结果快照（详情弹窗「执行结果」行展示）；入队即拒的失败为空串
            qms_json=json.dumps(qms_snap, ensure_ascii=False) if qms_snap else "",
            strm_json=json.dumps(strm_snap, ensure_ascii=False) if strm_snap else "",
            duration=duration,
            logs_json=json.dumps(t["logs"], ensure_ascii=False),
            )
        s.add(hist)
        s.commit()
        return hist.id


def _finish_kw(t: dict) -> dict:
    """本链路的收尾参数（差异只用参数表达，见 core.finish）：source=auto、Record 后再写
    RunHistory（_sync_pa_task）并把后台链路挂到 RunHistory.id、失败推送走 auto 来源开关。

    enabled_gate 按任务停用状态求值（T-011-F2 收拢进来）——改前 _finish 内部的
    `t.get("enabled", True)` 判断：**停用任务的失败不推送**。每次调用现取，
    包成函数而不是模块级 dict 就是为了这个求值时机。"""
    return dict(
        record_source="auto",
        hist_sink="pa",
        notify_kind="auto_fail",
        enabled_gate=t.get("enabled", True),
        run_history_writer=_sync_pa_task,
        strm_link_resolver=_resolve_strm_link,
    )
