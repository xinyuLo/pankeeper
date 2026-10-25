"""QMS 刮削结果回填：把「已触发」升级成真实结果，写回该次运行记录。

背景（2026-10-04 用户实锤）：`qms.trigger_scrape` 只证明 QMS **受理了请求**
（HTTP 200 + code 0），不是刮削成败；快照在触发那一刻就写死成「成功」，
于是 QMS 侧 `35~38.4k_1002164941.mp4` 全 scrape_failed，PanKeeper 详情还亮着绿色。

做法：转存收尾落库（拿到 run id）后 spawn 守护线程，按**本次实际转存的文件名**轮询
`GET /api/scrape/records` 到终态，把汇总（成功 N / 失败 M / 未见记录 K）写回
`run_history.qms_json`。轮询间隔与终态口径复用 media_push，但**不依赖推送开关**——
结果展示是独立诉求，推送关着也得回填。

STRM 2026-10-13 起轮询 /api/sync/records 到终态，快照如实写「已生成（新增 N 个 STRM）/ 同步失败：
原因」；等不到记录才回落「已触发」（QMS 定向临时任务此前无痕是误判，其实全程留痕、只留 7 天）。

⚠️ 曾有一段常驻「自愈复查」（每 3 分钟扫 24h 内未成功的运行自动翻正），2026-10-04 用户要求
**关闭**（不想让后端全天候轮询）：已删除，只保留「转存收尾 / 手动重刷」两条触发时轮询。
QMS 晚成功后不再自动翻正——要翻正就点任务行的「重新触发 QMS」（会清失败记录+重刮+回填）。
"""
from __future__ import annotations

import json
import os
import threading
import time

from ..db import SessionLocal
from ..models import PaTask, RunHistory
from ..adapters.base import AdapterError, is_video_file
from . import qms

POLL_INTERVAL = 30        # 轮询间隔（秒）
POLL_TIMEOUT = 30 * 60    # 最长等 30 分钟，超时按已到记录回填
NO_RECORD_TIMEOUT = 10 * 60  # 一条记录都没查到时的等待上限（QMS 受理后通常秒级建记录）
DEDUP_WAIT = 120          # 手动重刷用：等「新记录出现」的上限（超时=QMS 去重没重刮）
SCRAPE_POLL_INTERVAL = 10    # 等刮削完成的轮询间隔（秒）
SCRAPE_WAIT_TIMEOUT = 5 * 60  # 等刮削完成的上限（超时就不触发 STRM）
CLOCK_TOLERANCE = 60      # updated_at 判据的时钟容差（秒）：QMS 在 NAS、PanKeeper 在另一台机器，
                          # 两边时钟差几秒就会让 `updated_at >= 触发时刻` 误判为假（桩测试实测踩到）
PAGE_SIZE = 1000          # 每页条数（2026-10-13 实测 pageSize=1000 照单全收，响应带 total）
RECORD_MAX_PAGES = 5      # 记录窗口读取页数上限：5 页 = 5000 条，覆盖当前全池（2509）仍有余量
SYNC_POLL_INTERVAL = 5    # STRM 同步任务记录轮询间隔（秒）
SYNC_WAIT_TIMEOUT = 120   # 等同步任务终态上限（实测定向任务 1~5s 完成；超时回落「已触发」）


def video_names(names) -> list[str]:
    """转存清单里的视频文件（QMS 等待/未见统计的口径，杂件一律豁免）。

    扩展名白名单在 adapters/base.VIDEO_EXTS（与目录「过滤其他文件」开关同一份）。"""
    return [n for n in (names or []) if n and is_video_file(n)]

# STRM 触发结果登记（run_id → {st, cls}）：media_push 的推送线程在 QMS 终态后来取，
# 信息条才能如实显示「STRM 已生成」（进程重启即空——推送顶多按"状态未知"显示）。
_STRM_RESULTS: dict[int, dict] = {}

# 记录快照变更版本号：_write/_write_strm 每次落库自增，SSE（/api/queue/events）据此
# 向前端推具名事件 records，搜索历史页订阅它实时自刷（替代轮询）
_records_lock = threading.Lock()
_records_version = 0


def touch_records() -> None:
    global _records_version
    with _records_lock:
        _records_version += 1


def records_version() -> int:
    with _records_lock:
        return _records_version


def write_qms_interim(run_id: int, st: str, table=RunHistory) -> None:
    """整理徽标的**中间态**（已触发/正在刮削/整理中）：回填终态前跟随 QMS 真实进度。
    终态已落、或内容没变，就不写（省得 SSE 空推）。"""
    with SessionLocal() as s:
        r = s.get(table, run_id)
        if r is None:
            return
        cur = _load_snap(r.qms_json) or {}
        if cur.get("cls") in ("t-ok", "t-bad") or cur.get("st") == st:
            return
        r.qms_json = json.dumps({"st": st, "cls": "t-run"}, ensure_ascii=False)
        s.commit()
    touch_records()


def _qms_interim_text(st_path: dict | None, rows: list[dict]) -> str:
    """从 QMS 信号推导整理中间态文案：文件级（记录行的 path_is_scraping/path_is_renaming）
    优先，路径级（scrape pathes 的 is_running/is_scraping）兜底。"""
    if rows:
        if any(r.get("path_is_renaming") for r in rows):
            return "整理中"
        if any(r.get("path_is_scraping") for r in rows):
            return "正在刮削"
        return "已触发"
    if st_path:
        running = int(st_path.get("is_running") or 0)
        if running == 1:
            return "已触发（QMS 队列中）"
        if running == 2 or st_path.get("is_scraping"):
            return "正在刮削"
    return "已触发"


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
    for r in _records_window() or []:
        fn = r.get("file_name")
        if fn in want and fn not in out:
            out[fn] = [r.get("id"), r.get("status")]
    return out


def _records_max_id() -> int:
    """记录池当前最大 id（认领分界线：只认比它新的记录 = 本次触发产生的）。

    取不到（QMS 未启用/接口抖动）返回 0——认领退化为「不限 id」，靠原始路径兜精度。"""
    rows = qms.scrape_records(page_size=PAGE_SIZE) or []
    return max((int(r.get("id") or 0) for r in rows), default=0)


def _records_window(since_id: int = 0) -> list[dict]:
    """按页走读记录池：从最新一页往回翻，直到某页最小 id 已小于分界线 since_id
    （更旧的与本次无关，不再读）、不足一页（到底了）或达到页数上限。
    since_id=0 = 不设界，读到上限（覆盖当前全池）。

    2026-10-13 清单一：此前所有读取只拉全局最新一页，并发大批量转存时本场的记录
    会在等待窗内被挤出窗口，回填/推送/认领全线失明。分页 + 边界后悬崖消失。"""
    out: list[dict] = []
    for page in range(1, RECORD_MAX_PAGES + 1):
        rows = qms.scrape_records(page_size=PAGE_SIZE, page=page) or []
        if not rows:
            break
        out.extend(rows)
        page_min = min(int(r.get("id") or 0) for r in rows)
        if (since_id and page_min < since_id) or len(rows) < PAGE_SIZE:
            break
    return out


def claim_records(names: set[str], since_id: int = 0) -> dict[str, dict]:
    """认领本次转存的刮削记录：file_name 在清单内、id > since_id；同名取最新一条
    （记录按新→旧返回）。

    曾经还按「原始路径落在转存落点下」过滤（source_full_path 前缀），2026-10-13 晚
    NAS 实录翻车：DD 配置路径是用户手填的（/1.影视/待整理-电影），与 openlist 命名空间
    （/kuake/1.影视/待整理-电影，带账号根）对不上，前缀永不命中 → 认领为空 → 定向同步
    静默回退全库（v1.0.11 独行月球场）。谓词废弃——旧记录冒充由 since_id 分界挡住，
    同窗同名并发是良性过度同步（dest 归属照旧由记录自己的刮削路径决定）。"""
    want = {n for n in (names or set()) if n}
    out: dict[str, dict] = {}
    for r in _records_window(since_id) or []:
        fn = r.get("file_name")
        if fn not in want or fn in out or int(r.get("id") or 0) <= since_id:
            continue
        out[fn] = r
    return out


def watch_qms(run_id: int, task_name: str, names: list[str], baseline: dict[str, list] | None = None, table=RunHistory, on_state=None) -> None:
    """spawn 后台线程回填 QMS 真实结果（无文件/无任务名直接返回）。

    baseline：**触发前**的记录指纹（`record_fingerprint`，在 `qms.trigger_scrape` 之前取）。
    2026-10-06 起转存路径也必传——回填只认本次触发产生的新记录：一是 QMS 按文件路径去重
    （没重刮）时不能把旧结果当新结果，二是同名裸名文件跨剧碰撞（狂飙 17.mp4 撞兰香如故
    旧批次同名记录）时不能把别人的结果记到自己头上。
    table：快照写回哪张表 —— RunHistory（自动转存）/ Record（搜索转存，2026-10-04 起同款回填）。
    on_state：中间态回调（st 文案，30s 一次）——整理徽标跟 QMS 真实进度走（write_qms_interim）。
    """
    names = [n for n in (names or []) if n]
    if not run_id or not names:
        return
    threading.Thread(target=_watch, args=(run_id, task_name or "", names, baseline or {}, table, on_state), daemon=True).start()


def _scrape_dest_dirs(names: set[str], since_id: int = 0) -> list[str]:
    """从 QMS 刮削记录推本次文件**整理后的目录**（openlist 命名空间，strm 定向同步的目标）。

    记录只认 claim_records 认领到的（file_name 在清单内 + id > 触发前分界）——最早的
    按原始路径前缀认领因 DD 配置与 openlist 命名空间不一致废弃（2026-10-13 晚 NAS 实录）。
    QMS 刮削会把文件从待整理目录搬进正式目录：record.new_path 是相对整理目标根的
    新路径，目标根 = 该记录所属刮削路径的 dest_path（按 record.path 前缀匹配
    source_path 找归属）。"""
    sps = qms.scrape_pathes() or []
    claimed = claim_records(names, since_id)
    dests: dict[str, None] = {}
    for rec in claimed.values():
        # 剧集（tvshow）记录 QMS 不回填 path（只有电影有，2026-10-14 玉子市场实锤）：
        # 兜 source_full_path（文件全路径，两型都有），前缀归属语义不变，否则剧集必回退整路径同步
        src = (rec.get("path") or rec.get("source_full_path") or "").strip()
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


def _await_sync_done(dests: list[str], local: str, fired_ts: int) -> dict[str, dict] | None:
    """轮询 QMS 同步任务记录，等本次**定向同步**到终态（2026-10-13 用户定稿：要确定性结果，
    不要「已触发」假终态——出问题如实标失败，用户自己去 QMS 调整）。

    认领规则：remote_path 归一化 == dest、local_path == 同步落点、created_at ≥ 触发时刻-5s
    （防串台：同目录的旧任务不算）。全部 dest 都有终态（status 2=完成 / 3=失败）才收工；
    超时（SYNC_WAIT_TIMEOUT）把已认领的一并返回——调用方对「一条没有 / 没凑齐 / 没到终态」
    一律回落「已触发」+ Emby 90s 定时旧口径。"""
    deadline = time.time() + SYNC_WAIT_TIMEOUT
    want = {d.strip("/") for d in dests}
    found: dict[str, dict] = {}
    while True:
        for r in qms.sync_records() or []:
            key = str(r.get("remote_path") or "").strip("/")
            if (
                key in want
                and key not in found
                and str(r.get("local_path") or "").strip("/") == local.strip("/")
                and int(r.get("created_at") or 0) >= fired_ts - 5
            ):
                found[key] = r
        if found and len(found) == len(want) and all(r.get("status") in (2, 3) for r in found.values()):
            return found
        if time.time() > deadline:
            return found or None
        time.sleep(SYNC_POLL_INTERVAL)


def _fire_strm(strm_id: int, names: set[str], since_id: int = 0) -> dict:
    """STRM 触发的统一入口：**定向优先，失败回退**。

    - 定向（QMS /api/sync/manual 临时任务）：只扫本次文件整理后的目录（dest_path），
      不再整路径扫库（用户 2026-10-04：一次转存就一个文件夹，没理由全库探测）。
      仅 openlist 类型的同步路径可用（用户现配就是；网盘直连类型需要真 path_id，
      PanKeeper 拿不到，直接回退）；
    - 定向落盘 = 同步路径 local_path + openlist 完整路径，与常规同步布局一致；
    - 任何失败 → 回退整路径同步（QMS 自己刷新 Emby）；定向是临时任务（跳过刷新），由
      PanKeeper 等同步记录到终态后逐目录刷 Emby（借 QMS 配的 Emby 地址/ApiKey，尊重其开关；
      2026-10-13 起快照按 /api/sync/records 如实升级：已生成 N 个 STRM / 同步失败：原因，
      等不到记录才回落「已触发」+ 90s 定时）。"""
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
        dests = [d for d in _scrape_dest_dirs(names, since_id) if (d.strip("/") + "/").startswith(remote + "/")]
        if not dests:
            raise AdapterError("刮削记录里没有落在该同步路径下的目录")
        for d in dests:
            ok, msg = qms.manual_sync("0", d, local, account_id)
            if not ok:
                raise AdapterError(f"QMS 拒绝定向同步 {d}：{msg}")
            print(f"[run-watch] STRM 定向同步：{d}（只扫本次目录）", flush=True)

        # 等 QMS 同步任务真终态（2026-10-13 用户定稿：要确定性结果，不要「已触发」假终态
        # ——出问题如实标失败，用户自己去 QMS 调整；实测定向任务 1~5s 完成）
        fired_ts = int(time.time())
        verified = _await_sync_done(dests, local, fired_ts)
        if verified is None or len(verified) < len(dests) or any(r.get("status") not in (2, 3) for r in verified.values()):
            # 等不到/没凑齐同步记录（超时/QMS 异常）：回落旧口径——「已触发」+ Emby 90s 定时补刷
            print(f"[run-watch] STRM 同步记录没等到终态（超时 {SYNC_WAIT_TIMEOUT}s 或 QMS 异常），快照回落「已触发」、Emby 走 90s 定时", flush=True)
            hint = local.rstrip("/") + "/" + dests[0].strip("/")
            emby_name = qms.emby_library_name(hint)
            watchable = os.path.isdir(local)

            def _emby_later() -> None:
                # 容器看得到 STRM 本地挂载时轮询 .strm 落盘即刻刷；看不到退 90s 定时；
                # 轮询 5 分钟没见到也照刷兜底
                if watchable:
                    deadline = time.time() + 300
                    while time.time() < deadline:
                        if os.path.isdir(hint) and any(f.endswith(".strm") for f in os.listdir(hint)):
                            break
                        time.sleep(5)
                else:
                    time.sleep(90)
                ok2, msg2 = qms.refresh_emby_library(path_hint=hint)
                # msg2 必须透出：按库成功=「已按库刷新「xx」」；空串=没匹配到库退的全库。
                # 只打「成功」两种结局长得一样（2026-10-13 路径拼接 bug 借这个盲区藏了一整晚）
                print(
                    f"[run-watch] Emby 媒体库刷新（定向同步补，{'落盘触发' if watchable else '90s 定时'}）："
                    + (f"成功 · {msg2 or '按库未命中，退全库刷新'}" if ok2 else msg2),
                    flush=True,
                )

            threading.Thread(target=_emby_later, daemon=True).start()
            return {"st": "已触发", "cls": "t-ok", "emby": emby_name}

        # 同步记录到终态：逐目录刷 Emby（同步完成即刷，替代 90s 盲等），所属库名进推送
        time.sleep(2)  # 缓 2s 等落盘句柄收尾
        ok_recs = [(d, r) for d, r in verified.items() if r.get("status") == 2]
        bad_recs = [(d, r) for d, r in verified.items() if r.get("status") == 3]
        new_strm = sum(int(r.get("new_strm") or 0) for _, r in ok_recs)
        lib_names: list[str] = []
        for d, _r in ok_recs:
            hint_d = local.rstrip("/") + "/" + d.strip("/")
            ok2, msg2 = qms.refresh_emby_library(path_hint=hint_d)
            name = qms.emby_library_name(hint_d)
            if name:
                lib_names.append(name)
            print(
                f"[run-watch] Emby 媒体库刷新（同步完成触发，{d.strip('/')}）："
                + (f"成功 · {msg2 or '按库未命中，退全库刷新'}" if ok2 else msg2),
                flush=True,
            )
        emby_join = "／".join(lib_names)
        # 快照按同步记录如实升级；临时任务 ID=0 的口径不变（2026-10-04 用户纠正日志口径）
        if bad_recs:
            reason = "；".join(f"{d.strip('/')}：{r.get('fail_reason') or '未知原因'}" for d, r in bad_recs)
            st = f"同步失败：{reason}" + (f"（其余目录已生成 {new_strm} 个 STRM）" if new_strm else "")
            return {"st": st, "cls": "t-bad", "emby": emby_join}
        if new_strm:
            return {"st": f"新增 {new_strm} 个", "cls": "t-ok", "emby": emby_join}
        return {"st": "无新增", "cls": "t-ok", "emby": emby_join}
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
    log_fn=None,
    ride: bool = False,
) -> None:
    """等 QMS 刮削**真的跑完**，再等 delay 秒，才触发 STRM 同步（对齐参照项目 bdsavepro 语义）。

    为什么不能只等固定秒数（原实现）：刮削比间隔慢时，STRM 会在刮完前触发 → 生成的 strm
    可能基于不完整的元数据（用户 2026-10-04 追问过这个差异，当场改成真等）。

    完成判据 2026-10-13 起两条取或：
    (a) 路径状态（QMS `GET /api/scrape/pathes/{id}` 实测字段）：**曾亲眼看到它忙
        （is_running≠0 或 is_scraping）→ 现在不忙了 = 跑完**。updated_at 只作辅助
        （触发后被更新过也算证据）——实测它是路径行的更新时间，不一定随每次刮削前进
        （2026-10-04：刚触发时它还是 12 分钟前的值）。
    (b) 记录池认领：本次转存的全部视频文件都有「id > 触发前分界」的终态记录
        （认领规则见 claim_records）。为什么需要：快刮（首个轮询间隔内跑完）+
        updated_at 不前进时 (a) 的两条证据都会缺席，旧实现只能干等超时、STRM 永不触发
        （2026-10-13 定稿）。

    ride=True（搭车，2026-10-15）：本次 QMS 触发因「任务已存在」被拒、同路径刮削已在跑，
    等的是**代刮**产生的记录。此时 (a) 的「路径空闲」不可信——那把刮削可能开扫在本次
    文件落盘之前，扫完时根本没带上我的文件（银河护卫队2 实录：靠 1 分钟后第三部的触发
    才被代刮）——所以路径空闲但记录池还没认领到本次文件时继续等，只认 (b)。

    取不到路径状态（QMS 未启用/接口异常）→ 退化成"等 delay 秒直接触发"，不把能力弄丢。
    超时（默认 5 分钟）→ 记「未确认（…超时，未触发 STRM）」，**不触发 STRM**（宁缺勿错）。

    全程走后台守护线程：自动流程的队列 worker 不能被这几分钟堵住。
    """
    def _job() -> None:
        trigger_ts = int(time.time())
        deadline = time.time() + max(1, int(timeout))
        # 本次要等的视频文件 + 记录池 id 分界：完成判据 (b)、失败判定、定向目录推导
        # 三处共用同一份认领（claim_records）
        names = video_names(_record_names(run_id, table))
        since_id = _records_max_id() if names else 0
        reached = False
        degraded = False
        seen_busy = False  # 是否亲眼见过这条刮削路径在忙（本次触发真的跑起来了的硬证据）
        want = set(names)
        while True:
            st = qms.scrape_path_status(qms_id)
            if st is None:
                degraded = True
                break
            busy = int(st.get("is_running") or 0) != 0 or bool(st.get("is_scraping"))
            updated = int(st.get("updated_at") or 0)
            if busy:
                seen_busy = True
            claimed = claim_records(want, since_id) if want else {}
            all_terminal = bool(claimed) and want <= claimed.keys() and all(
                r.get("status") in TERMINAL_STATUS or r.get("status") in IGNORE_STATUS
                for r in claimed.values()
            )
            # 整理徽标跟 QMS 真实进度走（反正都在轮询）：已触发 → 正在刮削 → 整理中；
            # 全部终态就不写中间态，交给回填写终态
            if run_id and want and not all_terminal:
                write_qms_interim(run_id, _qms_interim_text(st, list(claimed.values())), table)
            if not busy and (seen_busy or updated >= trigger_ts - CLOCK_TOLERANCE):
                # 搭车模式路径空闲 ≠ 我的文件刮完了：那把刮削可能开扫在本次文件落盘之前，
                # 扫完没带上我（2026-10-15 银河护卫队2 实录）——必须等记录池认领到再定向
                if ride and want and not claimed:
                    pass
                else:
                    reached = True
                    break
            if all_terminal:
                reached = True
                print(f"[run-watch] QMS 刮削 #{qms_id} 完成（记录池判据：本次 {len(claimed)} 个文件全部终态"
                      + ("，搭车）" if ride else "）"), flush=True)
                break
            if time.time() > deadline:
                break
            time.sleep(SCRAPE_POLL_INTERVAL)

        if not reached and not degraded:
            tip = "搭车等待超时，未见本次文件的刮削记录" if ride else "刮削超时"
            snap = {"st": f"未确认（{tip}，未触发 STRM）", "cls": "t-bad"}
            if run_id:
                _write_strm(run_id, snap, table)
                _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap
            print(f"[run-watch] QMS 刮削 #{qms_id} 等待超时（{timeout}s），不触发 STRM", flush=True)
            if log_fn:
                log_fn("STRM 未生成：刮削超时未确认")
            return
        if degraded:
            print(f"[run-watch] 取不到 QMS 刮削状态，退化为等 {delay}s 后直接触发 STRM", flush=True)
        elif want:
            # QMS 刮削有失败 → 不生成 STRM（2026-10-04 用户："刮削失败了就不用生成 strm 了没意义"）。
            # 失败判定同样走认领——旧按名全池匹配会把同名旧失败记录算到本次头上；认领不到的
            # 文件算「未见」不算失败（与 _wait 口径一致）。想补救走「重新触发 QMS」
            failed = [fn for fn, r in claim_records(want, since_id).items() if r.get("status") in FAILED_STATUS]
            if failed:
                snap = {"st": f"QMS 刮削失败 {len(failed)} 项，未生成 STRM", "cls": "t-bad"}
                if run_id:
                    _write_strm(run_id, snap, table)
                    _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap
                print(f"[run-watch] QMS 刮削有失败（{failed}），不触发 STRM #{strm_id}", flush=True)
                if log_fn:
                    log_fn(f"STRM 未生成：QMS 刮削失败 {len(failed)} 项")
                return

        # 中间态先落库：记录页 STRM 徽标转蓝「正在生成 STRM」（推送仍等终态登记）
        if run_id:
            _write_strm(run_id, {"st": "正在生成 STRM", "cls": "t-run"}, table)
        if log_fn:
            log_fn("正在生成 STRM…")
        time.sleep(max(0, int(delay)))
        # 定向同步（只扫本次文件整理后的目录）需要本次文件名集合；拿不到就整路径
        names_set = set(names) if names else set(_record_names(run_id, table))
        if names_set:            snap = _fire_strm(strm_id, names_set, since_id)
        else:
            ok, msg = qms.trigger_strm(strm_id)
            # 「已触发」用绿色（t-ok）：触发成功就是这条链路的最好结局
            # （2026-10-04 用户要求改绿；QMS 的"已触发"保持灰——它随后会被真实结果回填替换）
            snap = {"st": "已触发" if ok else f"失败 · {msg}", "cls": "t-ok" if ok else "t-bad"}
        if run_id:
            _write_strm(run_id, snap, table)
            _STRM_RESULTS[f"{table.__name__}:{run_id}"] = snap  # 推送线程（media_push._wait_strm）来取
        if log_fn:
            log_fn(("STRM 生成成功" if snap.get("cls") == "t-ok" else "STRM 未生成")
                   + (f"：{snap['st']}" if snap.get("st") and snap.get("st") != "已触发" else ""))
        print(f"[run-watch] STRM 联动（同步目录 #{strm_id}）：{snap['st']}", flush=True)

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
    """只更新该次运行的 STRM 快照（任务行的整单判定只看 QMS，别混进来）；
    终态顺带在执行日志补一行（STRM 结果由后台线程产生，晚于收尾落库）。"""
    with SessionLocal() as s:
        r = s.get(table, run_id)
        if r is None:
            return
        r.strm_json = json.dumps(snap, ensure_ascii=False)
        if snap.get("cls") in ("t-ok", "t-bad"):
            txt = ("STRM 生成成功" if snap.get("cls") == "t-ok" else "STRM 未生成") + \
                  ("" if snap.get("st") in (None, "", "已触发") else f"：{snap['st']}")
            try:
                logs = json.loads(r.logs_json or "[]")
            except ValueError:
                logs = []
            if not logs or logs[-1].get("txt") != txt:
                logs.append({"lv": "STEP", "txt": txt})
            r.logs_json = json.dumps(logs, ensure_ascii=False)
        s.commit()
    touch_records()


def _load_snap(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _watch(run_id: int, task_name: str, names: list[str], baseline: dict[str, list], table=RunHistory, on_state=None) -> None:
    try:
        recs, verdict = _wait(names, baseline, on_state=on_state)
        if verdict == "dedup":
            # QMS 没产生新记录 = 按文件去重、根本没重刮：文件本来就在库里，不是"没刮到"。
            # 写终态快照，运行详情不再一直挂着「已触发」（T-008）；不拿旧结果冒充新结果。
            # ⚠️ 旧终态要看（T-008-F2）：baseline 里挂着失败态时，"没重刮"意味着那次失败
            # 仍是现状——不能粉饰成绿色「已在库」；全是 renamed 才算真在库。
            old_status = {v[1] for v in baseline.values() if isinstance(v, (list, tuple)) and len(v) > 1 and v[1]}
            if old_status & FAILED_STATUS:
                snap = {"st": "刮削失败（未重刮）", "cls": "t-bad"}
                print(f"[run-watch] record {run_id} QMS 未重刮且旧终态为失败 → 刮削失败（未重刮）", flush=True)
            else:
                snap = {"st": "已在库（未重刮）", "cls": "t-ok"}
                print(f"[run-watch] record {run_id} QMS 未产生新记录（按文件去重，未重刮）→ 已在库", flush=True)
            _write(run_id, snap, table)
            return
        _write(run_id, _summary(recs, names), table)
    except Exception as e:  # noqa: BLE001 —— 后台线程不能把异常丢给队列
        print(f"[run-watch] 回填 QMS 结果失败（record {run_id}）：{e}", flush=True)


def _fresh_map(latest: dict[str, dict], baseline: dict[str, list]) -> dict[str, dict]:
    """按名字挑出「本次触发产生的」新记录：触发前没记录 / 记录 id 更大 / 状态被就地更新。

    为什么必须逐名判定（2026-10-06 实锤）：QMS 记录只有 file_name 没有目录归属，
    同名裸名文件跨剧碰撞（狂飙的 17.mp4 ↔ 兰香如故旧批次的 17.mp4）时，旧记录会被
    误当成这次的刮削结果——推送标题张冠李戴、回填把别人的"成功"记到自己头上。
    baseline 指纹（触发前的 {名字: [记录id, 状态]}）是唯一可靠的分界线。"""
    out: dict[str, dict] = {}
    for fn, r in latest.items():
        base = baseline.get(fn)
        if base is None:
            out[fn] = r  # 触发前没记录：现在有 = 本次产生的
        elif r.get("id") is not None and base[0] is not None and r.get("id") > base[0]:
            out[fn] = r  # id 更大 = 重刮产生了新记录
        elif len(base) > 1 and base[1] is not None and r.get("status") != base[1]:
            out[fn] = r  # 同一条被就地更新（状态变了）也算新
    return out


def _wait(names: list[str], baseline: dict[str, list] | None = None, on_state=None) -> tuple[list[dict], str]:
    """轮询到「本次转存的文件**全部**出现本次触发产生的终态记录」，或停滞/总超时。

    返回 (记录, verdict)。verdict：
    - done    全部文件都有本次的新终态记录（或全是 ignore）
    - dedup   触发前就有记录的文件全部原状、记录池纹丝不动 → QMS 按路径去重没重刮
    - stale   记录池超过 NO_RECORD_TIMEOUT 无新增（QMS 不再产出）→ 有什么报什么
    - timeout 总超时（POLL_TIMEOUT）→ 按已有记录回填

    ⚠️ 三个历史教训都钉死在这里，别改回去：
    1. 不按 name= 筛：QMS 的 name 过滤实测会漏记录（2026-10-04：41.4k.mp4 明明有
       scrape_failed 记录，带 name 查却查不到）。一次拉最新 N 条按 file_name 精确匹配。
    2. **必须全覆盖才收工**（2026-10-06 实锤）：旧判定只看"已匹配的记录是否全部终态"，
       没管还没出现记录的文件——6 个新文件只等到 1 个旧记录就全数返回，推送变成
       「更新 1 集」、回填写成"成功 1 / 未见记录 5"（其实 QMS 后来把 6 个全刮好了）。
    3. **只认本次触发后的新记录**（2026-10-06 实锤）：旧记录一律不算这次的账，
       否则同名旧记录顶替（狂飙 17.mp4 撞兰香如故旧记录，推送变成"兰香如故 · 更新 1 集"）。
    """
    baseline = baseline or {}
    want = set(video_names(names))
    if not want:  # 清单里没有视频（纯杂件）→ QMS 本就无可等记录，立即放行
        return [], "done"
    # 记录窗口分界：基线里最旧的记录 id——再往前都是与本次无关的历史，不必读
    base_ids = [v[0] for v in baseline.values() if v and v[0]]
    base_watermark = min(base_ids) if base_ids else 0
    deadline = time.time() + POLL_TIMEOUT
    stale_deadline = time.time() + NO_RECORD_TIMEOUT
    dedup_deadline = time.time() + DEDUP_WAIT
    seen_max_id = 0
    initial_max_id: int | None = None
    while True:
        rows = _records_window(base_watermark)
        max_id = max((int(r.get("id") or 0) for r in rows), default=0)
        if initial_max_id is None:
            initial_max_id = max_id
        if max_id > seen_max_id:
            seen_max_id = max_id
            stale_deadline = time.time() + NO_RECORD_TIMEOUT  # 记录池还在产出：停滞时钟重置
        latest: dict[str, dict] = {}
        for r in rows:
            # 记录按新→旧返回：第一次遇到的名字就是该文件最新的那条
            fn = r.get("file_name")
            if fn in want and fn not in latest:
                latest[fn] = r
        fresh = _fresh_map(latest, baseline)
        alive = [r for r in fresh.values() if r.get("status") not in IGNORE_STATUS]
        missing = want - set(fresh)
        now = time.time()
        if on_state:
            # 整理徽标跟 QMS 真实进度走：文件级标志优先（刮削/整理中），没记录就是已触发待受理
            matched = [r for r in rows if r.get("file_name") in want]
            try:
                on_state(_qms_interim_text(None, matched))
            except Exception:  # noqa: BLE001 —— 中间态写失败不影响等待主流程
                pass
        if not missing and (not alive or all(r.get("status") in TERMINAL_STATUS for r in alive)):
            return list(fresh.values()), "done"
        if (
            baseline
            and not fresh
            and all(fn in baseline for fn in want)  # 所有文件触发前就有记录，才谈得上"被去重"
            and now > dedup_deadline
            and seen_max_id == initial_max_id  # 记录池纹丝不动：连别处的新记录都没有
        ):
            return [], "dedup"
        if now > stale_deadline:
            return list(fresh.values()), ("stale" if missing else "done")
        if now > deadline:
            return list(fresh.values()), "timeout"
        time.sleep(POLL_INTERVAL)


def _summary(recs: list[dict], names: list[str]) -> dict:
    """终态汇总 → 快照 {st, cls}（cls: t-ok 绿 / t-bad 红 / t-off 灰）。"""
    ok = sum(1 for r in recs if r.get("status") == "renamed")
    fail = sum(1 for r in recs if r.get("status") in FAILED_STATUS)
    # 「未见」只对视频文件成立：nfo/图片等杂件 QMS 过滤不刮，永远没有记录，不算未见
    missing = len(video_names(names)) - len({r.get("file_name") for r in recs})
    if not recs:
        return {"st": "未见刮削记录", "cls": "t-off"}
    if fail:
        st = f"成功 {ok} / 失败 {fail}" if ok else f"失败 {fail} 项"
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
            touch_records()
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
    touch_records()
    print(f"[run-watch] run {run_id} QMS 结果回填：{snap.get('st')}", flush=True)
