"""自动转存流程（定时调度 pa_scheduler 入队的任务，source=auto）。

⚠️ 与手动转存 manual.py 刻意分成两个文件、流程各自完整一份（允许代码重复）：
这边独有的业务——ShareBanned 熔断回写 PaTask、完成后回写任务状态 + RunHistory、
失败推送走 auto 来源开关。改这边的逻辑不要碰 manual.py，反过来也一样。
网盘 API 层在 adapters/ 下，同样每盘独立文件（baidu/quark/pan115 互不依赖）。
"""
from __future__ import annotations

import json
import re
import time

from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
from ..adapters.factory import make_adapter
from ..db import SessionLocal
from ..models import Account, DdItem, PaTask, Record, RunHistory
from ..services import media_push, notify, qms
from ..services.share_cache import build_payload, share_key, share_list_cache

MAX_LOGS = 40


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


def _push_log(t: dict, lv: str, txt: str) -> None:
    t["logs"].append({"lv": lv, "txt": txt})
    if len(t["logs"]) > MAX_LOGS:
        t["logs"].pop(0)


def _files_snap(files, spec, result) -> list:
    """分享内文件清单快照（记录详情「最近结果 → 详情」弹窗用）。
    done 记录里非新增的文件只有两种去向：去重跳过 / 未勾选；
    fail 记录没有逐文件结果，统一「未转存」。目录条目不进表。
    （与 manual.py 同款，两边刻意不共享。）"""
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
        # 排除清单不传给适配器——由本流程统一过滤并如实记录（excluded 名单要落库）
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
            _finish(eng, t, "warn", "链接已失效（分享内容为空）")
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

    # ---- 转存成功，走 QMS/STRM 联动（阶段机照前端契约，延迟可配） ----
    qms_snap, strm_snap = _media_chain(eng, t, cfg, result, name_head)
    _finish(eng, t, "done", qms_snap=qms_snap, strm_snap=strm_snap, result=result, files_snap=_files_snap(files, spec, result))


def _media_chain(eng, t: dict, cfg: dict, result, name_head: str) -> tuple[dict, dict]:
    """触发 QMS / STRM。链接来源：转存配置里 qms_on 的目录（按目标路径前缀匹配）。"""
    from ..services.settings_svc import get_group
    if get_group("media").get("backend", "qms") != "qms":
        # 联动后端切到 LitePan：按目录配的 lp_on/lp_event 推 webhook（未配的目录跳过）
        from ..services import litepan
        lp_link = resolve_litepan_link(t["path"], t.get("paTaskId"))
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

    _sleep_phase(eng, t, "waitqms", int(cfg.get("qms", 10)), f"等待 {cfg.get('qms', 10)} 秒后触发 QMS 刮削")
    t["phase"] = "qms"
    t["phaseStart"] = int(time.time() * 1000)
    # 触发**前**抓记录指纹：回填/推送只认本次触发产生的新记录（去重不重刮不冒充、
    # 同名旧记录不串台，见 run_watch._wait 的教训清单）
    from ..services import run_watch
    t["_qms_baseline"] = run_watch.record_fingerprint([e.get("name") for e in result.transferred])
    ok, msg = qms.trigger_scrape(link["qms_id"])
    # ⚠️ 这里只能证明"QMS 受理了触发请求"，不是刮削结果——快照如实写「已触发」，
    # 真实结果由 run_watch 后台轮询回填（2026-10-04 用户实锤：QMS 侧 scrape_failed
    # 而这边显示"成功"）。别改回"成功"，那是骗人。
    qms_snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-off" if ok else "t-bad"}
    _push_log(t, "INFO" if ok else "ERROR", f"QMS 刮削任务 #{link['qms_id']} 触发{'成功' if ok else '失败'}：{msg or '详见 QMS 侧日志'}")

    # STRM 不在这里等、也不在这里触发：正确做法是「等 QMS 刮削真跑完再触发」
    # （run_watch.trigger_strm_after_scrape：轮询 /api/scrape/pathes/{id} 到 is_running=0
    #  && is_scraping=false && updated_at>=触发时刻）。放这里会**堵住队列 worker 好几分钟**，
    # 所以改为收尾落库后由 _sync_pa_task 挂后台线程（2026-10-04 用户要求对齐 bdsavepro 语义）。
    if link.get("strm_id"):
        strm_snap = {"st": "等待刮削完成…", "cls": "t-off"}
        _push_log(t, "STEP", f"STRM 联动已挂后台（同步目录 #{link['strm_id']}）：QMS 刮削成功后自动触发定向同步临时任务（成功才生成）")

    # 推送（watch_and_spawn）挪到 _sync_pa_task 落库后：那里才有 run_id，推送线程才能
    # 等 STRM 触发结果、信息条如实显示「STRM 已生成」（2026-10-04 用户要求）
    return qms_snap, strm_snap


def _match_dd_link(path: str) -> dict | None:
    """按保存目录前缀匹配「转存配置」里开了 QMS 的目录（**目录级兜底**）。"""
    with SessionLocal() as s:
        for d in s.query(DdItem).filter(DdItem.qms_on.is_(True), DdItem.qms_id.isnot(None)).all():
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                return {"qms_id": d.qms_id, "strm_id": d.strm_id}
    return None


def _hit_dd_dir(path: str) -> dict | None:
    """save_dir 命中的转存配置目录（最长前缀优先）。

    ⚠️ **刻意不过滤 `qms_on`**：调用方需要能区分「没登记这个目录」和「登记了但把 QMS 关了」。
    后者代表用户明确关掉了联动开关 —— 那时任务级配置也该一并作废（2026-10-04 用户定稿）。
    """
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
                    hit = {"path": base, "qms_on": bool(d.qms_on), "qms_id": d.qms_id, "strm_id": d.strm_id}
    return hit


def resolve_media_link(path: str, task_id: int | None = None) -> dict | None:
    """解析一个任务要用的 QMS/STRM 联动目标。

    判定顺序（2026-10-04 用户定稿："那个目录要是关闭了，就等于没配"）：
    ① **总闸**：`save_dir` 命中的「转存配置」目录若 `qms_on=False` → **一律视为未配**，
       哪怕任务弹窗里显式选了 QMS/STRM 目录也不联动（否则会出现"目录明明关了、任务还在偷偷联动"
       这种没人能预期的事）；
    ② 总闸开着（或 `save_dir` 没命中任何目录）时：**任务弹窗里选的 `qms_id`/`strm_id` 优先**，
       没填的字段各自回退到命中目录的值。

    历史：执行侧原只读目录级 DdItem，任务弹窗配的 `strm_id` 被无视 —— 用户任务 1 配了 5，
    代码却拿到目录的 None，STRM 永远不触发（2026-10-04 用户实拍发现）。
    """
    qms_id: int | None = None
    strm_id: int | None = None
    if task_id:
        with SessionLocal() as s:
            row = s.get(PaTask, task_id)
            if row is not None:
                qms_id, strm_id = row.qms_id, row.strm_id
    hit = _hit_dd_dir(path or "")
    if hit is not None and not hit["qms_on"]:
        return None  # 目录关了联动 → 未配（任务里选过什么一律作废）
    dq = hit["qms_id"] if hit else None
    ds = hit["strm_id"] if hit else None
    rq = qms_id or dq
    if not rq:
        return None
    rs = strm_id if strm_id is not None else ds
    if rs is None:
        # STRM 跟随 QMS 自动配对（2026-10-04 定稿：转存配置里不再单独选 STRM）
        from .qms import strm_id_for_qms
        rs = strm_id_for_qms(rq)
    return {"qms_id": rq, "strm_id": rs}


def resolve_litepan_link(path: str, task_id: int | None = None) -> dict | None:
    """LitePan 联动目标（2026-10-06 用户定稿）：按 save_dir 前缀匹配「转存配置」里
    lp_on=True 的目录，事件名**按目录配**——不同目录推不同 LitePan 自动化规则
    （电影/电视剧各一条），全局单一事件名不够用。

    **三道闸**（2026-10-06 用户逐步定稿）：
    ① 设置页 LitePan「启用联动」关 → 全部不推；
    ② save_dir 没命中任何 lp_on 目录 → 一律 None（任务/弹窗事件全部作废，没配就不让选）；
    ③ **事件名没填就不联动**（无全局兜底）：任务弹窗和目录配置都没填事件 → None。
    事件名优先级：任务弹窗 > 目录配置。"""
    from ..services.settings_svc import get_group
    if not get_group("litepan").get("enabled"):
        return None
    hit: DdItem | None = None
    with SessionLocal() as s:
        for d in s.query(DdItem).filter(DdItem.lp_on.is_(True)).all():
            if path == d.path or path.startswith(d.path.rstrip("/") + "/"):
                if hit is None or len(d.path) > len(hit.path):
                    hit = d  # 最长前缀优先
    if hit is None:
        return None
    task_event = ""
    if task_id:
        with SessionLocal() as s:
            row = s.get(PaTask, task_id)
            if row is not None:
                task_event = (row.lp_event or "").strip()
    dir_event = (hit.lp_event or "").strip()
    event = task_event or dir_event
    if not event:
        return None  # 事件名没填 = 不联动（无兜底）
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


def _finish(eng, t: dict, status: str, message: str = "", qms_snap: dict | None = None, strm_snap: dict | None = None, result=None, files_snap: list | None = None) -> None:
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
                source="auto",  # 自动转存：记录页不展示，走「转存历史」页 + 任务内转存日志
                backend=get_group("media").get("backend", "qms"),
                logs_json=json.dumps(t["logs"], ensure_ascii=False),
                files_json=json.dumps(files_snap or [], ensure_ascii=False),
            )
        )
        s.commit()
    _sync_pa_task(t, status, result, qms_snap, strm_snap)
    if status == "fail" and t.get("enabled", True):
        # 推送统一走「推送通知」的全局开关（on_auto 时机）；只推启用中的任务
        notify.push("转存失败", f"{t['name']}：{message or '未知原因'}", kind="auto_fail")


def _sync_pa_task(t: dict, status: str, result, qms_snap: dict | None = None, strm_snap: dict | None = None) -> None:
    """自动任务（带 paTaskId）完成/失败后回写任务状态与执行历史。

    add/skip 统计只有成功路径才有 TransferResult；入队即拒（缺链接/无适配器）
    的失败 result 为 None，历史里记 0 即可——失败原因已在任务日志里。
    转存日志统计（分享数/正则未命中/MD5 跳过/转存与排除文件名/起止秒级时间）
    从 t["_runStats"] 收——run_auto 里边跑边填。
    """
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
            # QMS/STRM 联动结果快照（详情弹窗「执行结果」行展示）；入队即拒的失败为空串
            qms_json=json.dumps(qms_snap, ensure_ascii=False) if qms_snap else "",
            strm_json=json.dumps(strm_snap, ensure_ascii=False) if strm_snap else "",
            duration=duration,
            logs_json=json.dumps(t["logs"], ensure_ascii=False),
            )
        s.add(hist)
        s.commit()
        run_id_new = hist.id
    # 触发后的两件后台事（都不阻塞队列，见 services/run_watch.py）：
    # ① QMS 真实结果回填（"触发受理"≠"刮削成功"）；② 等刮削真跑完再触发 STRM
    if qms_snap and qms_snap.get("st") == "已触发" and result and result.transferred:
        from ..services import media_push, run_watch
        from ..services.settings_svc import get_group

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
        # ③ 推送：ctx 带 run_id + STRM 计划 + 触发前指纹（推送只认本次的新记录），
        #    推送线程会等 STRM 触发结果再发，信息条才能如实显示「STRM 已生成」
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
