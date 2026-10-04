"""QMS 刮削结果回填：把「已触发」升级成真实结果，写回该次运行记录。

背景（2026-10-04 用户实锤）：`qms.trigger_scrape` 只证明 QMS **受理了请求**
（HTTP 200 + code 0），不是刮削成败；快照在触发那一刻就写死成「成功」，
于是 QMS 侧 `35~38.4k_1002164941.mp4` 全 scrape_failed，PanKeeper 详情还亮着绿色。

做法：转存收尾落库（拿到 run id）后 spawn 守护线程，按**本次实际转存的文件名**轮询
`GET /api/scrape/records` 到终态，把汇总（成功 N / 失败 M / 未见记录 K）写回
`run_history.qms_json`。轮询间隔与终态口径复用 media_push，但**不依赖推送开关**——
结果展示是独立诉求，推送关着也得回填。

STRM 暂不轮询（QMS 侧没有对应查询接口，只能证明「已触发」），快照如实写「已触发」。

⚠️ 曾有一段常驻「自愈复查」（每 3 分钟扫 24h 内未成功的运行自动翻正），2026-10-04 用户要求
**关闭**（不想让后端全天候轮询）：已删除，只保留「转存收尾 / 手动重刷」两条触发时轮询。
QMS 晚成功后不再自动翻正——要翻正就点任务行的「重新触发 QMS」（会清失败记录+重刮+回填）。
"""
from __future__ import annotations

import json
import threading
import time

from ..db import SessionLocal
from ..models import PaTask, RunHistory
from ..adapters.base import AdapterError
from . import qms

POLL_INTERVAL = 30        # 轮询间隔（秒）
POLL_TIMEOUT = 30 * 60    # 最长等 30 分钟，超时按已到记录回填
NO_RECORD_TIMEOUT = 10 * 60  # 一条记录都没查到时的等待上限（QMS 受理后通常秒级建记录）
DEDUP_WAIT = 120          # 手动重刷用：等「新记录出现」的上限（超时=QMS 去重没重刮）
SCRAPE_POLL_INTERVAL = 10    # 等刮削完成的轮询间隔（秒）
SCRAPE_WAIT_TIMEOUT = 5 * 60  # 等刮削完成的上限（超时就不触发 STRM）
CLOCK_TOLERANCE = 60      # updated_at 判据的时钟容差（秒）：QMS 在 NAS、PanKeeper 在另一台机器，
                          # 两边时钟差几秒就会让 `updated_at >= 触发时刻` 误判为假（桩测试实测踩到）
PAGE_SIZE = 500           # 记录拉取条数：不按任务名筛（实测会漏，见下）

# STRM 触发结果登记（run_id → {st, cls}）：media_push 的推送线程在 QMS 终态后来取，
# 信息条才能如实显示「STRM 已生成」（进程重启即空——推送顶多按"状态未知"显示）。
_STRM_RESULTS: dict[int, dict] = {}


def get_strm_result(run_id: int | None, table=RunHistory) -> dict | None:
    """取该次运行的 STRM 触发结果（后台线程完成时登记；没配/没跑完 → None）。"""
    if run_id is None:
        return None
    return _STRM_RESULTS.get(f"{table.__name__}:{run_id}")

# 与 media_push 同一套口径：renamed = 整理完成；两种 failed 也算"到了"
TERMINAL_STATUS = {"renamed", "scrape_failed", "rename_failed"}
IGNORE_STATUS = {"ignore"}
FAILED_STATUS = {"scrape_failed", "rename_failed"}


def record_fingerprint(names: list[str]) -> dict[str, list]:
    """触发**前**的记录指纹：{文件名: [记录 id, 状态]}（取该文件最新那条）。

    为什么需要：QMS 按文件路径去重——文件已有记录（失败记录也算）时再点触发，它
    **不会重新刮**。回填若只认"最新一条记录"，就会把旧结果当成这次重刷的结果写回去，
    用户看到的就是"点了没反应，状态不变"（2026-10-04 实锤：他删掉 QMS 旧记录后才真重刮）。
    """
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
    """spawn 后台线程回填 QMS 真实结果（无文件/无任务名直接返回）。

    baseline：触发前指纹。传了它就只认「更新的记录」——用于手动重刷，避免 QMS 去重
    （没真重刮）时把旧记录当成本次结果。不传（转存路径）则维持原判定逻辑。
    table：快照写回哪张表 —— RunHistory（自动转存）/ Record（搜索转存，2026-10-04 起同款回填，
    用户实锤：搜索转存 QMS 侧刮失败了这边还显示"触发成功"）。
    """
    names = [n for n in (names or []) if n]
    if not run_id or not names:
        return
    threading.Thread(target=_watch, args=(run_id, task_name or "", names, baseline or {}, table), daemon=True).start()


def _is_fresh(latest: dict[str, dict], baseline: dict[str, list]) -> bool:
    """是否出现了「比触发前更新」的记录（无 baseline 一律算新，走原逻辑）。"""
    if not baseline:
        return True
    for name, rec in latest.items():
        base = baseline.get(name)
        if base is None:
            return True  # 触发前没记录、现在有了 = 新
        if rec.get("id") is not None and base[0] is not None and rec.get("id") > base[0]:
            return True  # id 更大 = 重刮产生了新记录
        if rec.get("status") != base[1]:
            return True  # 同一条被就地更新（状态变了）也算新
    return False


def _scrape_dest_dirs(names: set[str]) -> list[str]:
    """从 QMS 刮削记录推本次文件**整理后的目录**（openlist 命名空间，strm 定向同步的目标）。

    QMS 刮削会把文件从待整理目录搬进正式目录：record.new_path 是相对整理目标根的
    新路径，目标根 = 该记录所属刮削路径的 dest_path（按 record.path 前缀匹配
    source_path 找归属）。按文件名精确匹配（同 run_watch 匹配纪律：不按 name= 筛）。"""
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
    """STRM 触发的统一入口：**定向优先，失败回退**。

    - 定向（QMS /api/sync/manual 临时任务）：只扫本次文件整理后的目录（dest_path），
      不再整路径扫库（用户 2026-10-04：一次转存就一个文件夹，没理由全库探测）。
      仅 openlist 类型的同步路径可用（用户现配就是；网盘直连类型需要真 path_id，
      PanKeeper 拿不到，直接回退）；
    - 定向落盘 = 同步路径 local_path + openlist 完整路径，与常规同步布局一致；
    - 任何失败 → 回退整路径同步（QMS 自己刷新 Emby）；定向是临时任务（跳过刷新），
      由 PanKeeper 延迟 90s 补一次 Emby 刷新（借 QMS 配的 Emby 地址/ApiKey，尊重其开关）。"""
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
            time.sleep(90)  # 等 QMS 临时任务跑完（单目录同步通常几十秒内）
            ok2, msg2 = qms.refresh_emby_library()
            print(f"[run-watch] Emby 媒体库刷新（定向同步补）：{'成功' if ok2 else msg2}", flush=True)

        threading.Thread(target=_emby_later, daemon=True).start()
        return {"st": "已触发（定向）", "cls": "t-ok"}
    except Exception as e:  # noqa: BLE001 —— 回退口：定向失败绝不把 STRM 弄丢
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
    """等 QMS 刮削**真的跑完**，再等 delay 秒，才触发 STRM 同步（对齐参照项目 bdsavepro 语义）。

    为什么不能只等固定秒数（原实现）：刮削比间隔慢时，STRM 会在刮完前触发 → 生成的 strm
    可能基于不完整的元数据（用户 2026-10-04 追问过这个差异，当场改成真等）。

    完成判据（QMS `GET /api/scrape/pathes/{id}` 实测字段 `is_running`/`is_scraping`/`updated_at`）：
    **曾亲眼看到它忙（is_running≠0 或 is_scraping）→ 现在不忙了 = 跑完**。
    为什么不能只认 `updated_at >= 触发时刻`（bdsavepro 的判据）：实测该字段是**路径行的更新时间**，
    不一定随每次刮削前进（2026-10-04 实测：刚触发时它还是 12 分钟前的值）——只认它会永远等不到、
    白白超时导致 STRM 再也不触发。所以 updated_at 只作为**辅助信号**（触发后被更新过也算证据）。
    取不到状态（QMS 未启用/接口异常）→ 退化成"等 delay 秒直接触发"，不把能力弄丢。
    超时（默认 5 分钟）→ 记「未确认（刮削超时，未触发 STRM）」，**不触发 STRM**（宁缺勿错）。

    全程走后台守护线程：自动流程的队列 worker 不能被这几分钟堵住。
    """
    def _job() -> None:
        trigger_ts = int(time.time())
        deadline = time.time() + max(1, int(timeout))
        reached = False
        degraded = False
        seen_busy = False  # 是否亲眼见过这条刮削路径在忙（本次触发真的跑起来了的硬证据）
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
            # QMS 刮削有失败 → 不生成 STRM（2026-10-04 用户："刮削失败了就不用生成 strm 了没意义"）
            # STRM 同步是目录级的，刮失败的文件生成了也是垃圾；想补救走「重新触发 QMS」（重刷成功会照常续上 STRM）
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
        # 定向同步（只扫本次文件整理后的目录）需要本次文件名集合；拿不到就整路径
        names_set = set(names) if names else set(_record_names(run_id, table))
        if names_set:            snap = _fire_strm(strm_id, names_set)
        else:
            ok, msg = qms.trigger_strm(strm_id)
            # 「已触发」用绿色（t-ok）：STRM 没有结果查询接口，触发成功就是这条链路的最好结局
            # （2026-10-04 用户要求改绿；QMS 的"已触发"保持灰——它随后会被真实结果回填替换）
            snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-ok" if ok else "t-bad"}
        if run_id:
            _write_strm(run_id, snap, table)
            _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap  # 推送线程（media_push._wait_strm）来取
        print(f"[run-watch] STRM #{strm_id} 触发：{snap['st']}", flush=True)

    threading.Thread(target=_job, daemon=True).start()


def _record_names(run_id: int, table=RunHistory) -> list[str]:
    """该次运行实际转存的文件名（QMS 刮削结果判定用）。RunHistory=transferred_json；Record=files_json。"""
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
    """只更新该次运行的 STRM 快照（任务行的整单判定只看 QMS，别混进来）。"""
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
        recs, fresh = _wait(task_name, names, baseline)
        if not fresh:
            # QMS 没产生新记录 = 按文件去重、根本没重刮：保留原判定，不拿旧结果冒充新结果
            print(f"[run-watch] record {run_id} QMS 未产生新记录（按文件去重，未重刮）→ 保持原判定", flush=True)
            return
        _write(run_id, _summary(recs, names), table)
    except Exception as e:  # noqa: BLE001 —— 后台线程不能把异常丢给队列
        print(f"[run-watch] 回填 QMS 结果失败（record {run_id}）：{e}", flush=True)


def _wait(task_name: str, names: list[str], baseline: dict[str, list] | None = None) -> tuple[list[dict], bool]:
    """轮询到「本次文件全部终态」或超时。每个文件名只取最新一条记录。

    返回 (记录, 是否出现新记录)。给了 baseline 时，若在 DEDUP_WAIT 内没有出现更新的
    记录，判定为 QMS 去重未重刮（fresh=False，调用方保持原判定）。

    ⚠️ 刻意**不按任务名筛**：QMS 的 name 过滤实测会漏记录（2026-10-04：41.4k.mp4 明明
    有 scrape_failed 记录，带 name 查却查不到，media_push 那套因此可能永远等不到终态）。
    改为一次拉最新 N 条、只按 file_name 精确匹配——记录是新→旧，刚转存的文件必在前排。
    """
    baseline = baseline or {}
    deadline = time.time() + POLL_TIMEOUT
    no_record_deadline = time.time() + NO_RECORD_TIMEOUT
    dedup_deadline = time.time() + DEDUP_WAIT
    want = set(names)
    while True:
        latest: dict[str, dict] = {}
        for r in qms.scrape_records(page_size=PAGE_SIZE) or []:
            # 记录按新→旧返回：第一次遇到的名字就是该文件最新的那条
            fn = r.get("file_name")
            if fn in want and fn not in latest:
                latest[fn] = r
        fresh = _is_fresh(latest, baseline)
        alive = [r for r in latest.values() if r.get("status") not in IGNORE_STATUS]
        if fresh and alive and all(r.get("status") in TERMINAL_STATUS for r in alive):
            return list(latest.values()), True
        if fresh and latest and not alive:
            return list(latest.values()), True  # 查到的全是 ignore：等于没得等
        now = time.time()
        if baseline and not fresh and now > dedup_deadline:
            return [], False  # 去重没重刮：别把旧结果当新结果，保持原判定
        if not latest and now > no_record_deadline:
            return [], fresh  # 10 分钟一条都没建：如实报"未见刮削记录"，别死等
        if now > deadline:
            return list(latest.values()), fresh  # 超时：按已有记录回填
        time.sleep(POLL_INTERVAL)


def _summary(recs: list[dict], names: list[str]) -> dict:
    """终态汇总 → 快照 {st, cls}（cls: t-ok 绿 / t-bad 红 / t-off 灰）。"""
    ok = sum(1 for r in recs if r.get("status") == "renamed")
    fail = sum(1 for r in recs if r.get("status") in FAILED_STATUS)
    missing = len(names) - len({r.get("file_name") for r in recs})
    if not recs:
        return {"st": "未见刮削记录", "cls": "t-off"}
    if fail:
        st = f"成功 {ok} / 失败 {fail}" if ok else f"失败 {fail} 项（详见 QMS）"
        return {"st": st, "cls": "t-bad"}
    if missing:
        return {"st": f"成功 {ok} / 未见记录 {missing}", "cls": "t-off"}
    return {"st": "成功", "cls": "t-ok"}


def overall_of(status: str, qms_snap: dict | None) -> dict:
    """整单结果 = 转存成败 + QMS 快照合成（列表/任务行/日志卡片共用这一份口径）。

    - 转存失败 → 失败（红）
    - 转存成功但 QMS 有失败 → **部分失败**（橙）——只看转存会把它误报成"成功"
    - 其余（含 QMS 未配置/已触发/未见记录）→ 成功（绿）
    """
    if status != "success":
        return {"st": "失败", "cls": "t-bad"}
    if (qms_snap or {}).get("cls") == "t-bad":
        return {"st": "部分失败", "cls": "t-warn"}
    return {"st": "成功", "cls": "t-ok"}


def _write(run_id: int, snap: dict, table=RunHistory) -> None:
    """把真实结果写回该次运行记录的 QMS 快照（不动任务的任何配置）。

    table=RunHistory（自动转存）时，若这次仍是该任务的最新一次运行，顺带把任务行的
    「最近结果」升级成整单口径（QMS 有失败 → 部分失败），否则任务行会一直显示"成功"，
    与详情自相矛盾；Record（搜索转存）没有任务行概念，只写快照。
    """
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
