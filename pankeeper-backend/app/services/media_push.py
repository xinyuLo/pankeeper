"""富文本推送（路线 A 联动）：等 QMS 刮削完成后查记录 + TMDB 拼 Markdown 推送。

流程：转存成功触发 QMS 刮削 → engine 调 watch_and_spawn → 本模块后台轮询刮削记录，
全部到达终态（renamed 或 失败态）后拼 desp 推送。desp 顶部是 PanKeeper 自定义的
信息条（网盘 · 任务名 / 转存集数 / QMS 结果 / STRM 结果），正文按
《Server酱推送格式规范》拼（多集合并一条，w500 图，h2 剧名 + h3 集行）。

兜底：QMS 超时 / 无可用记录 / TMDB 拿不到图 → 回退纯文字推送（带信息条+文件清单）；
失败通知仍走原通道（不带图）。
"""
from __future__ import annotations

import re
import threading
import time

from . import notify, qms, tmdb
from .settings_svc import get_group

POLL_INTERVAL = 30        # 轮询间隔（秒）；QMS 刮一部剧通常几分钟
POLL_TIMEOUT = 30 * 60    # 最长等 30 分钟，超时按已到记录推送
OVERVIEW_MAX = 200        # 简介截断长度（规范 §6.3）

# qMediaSync 记录的终态：renamed = 整理完成；两种 failed 也算"到了"（失败信息进信息条）
TERMINAL_STATUS = {"renamed", "scrape_failed", "rename_failed"}
IGNORE_STATUS = {"ignore"}   # 不会到任何终态，等待时剔除

DRIVE_META = {"baidu": ("🔵", "百度"), "quark": ("☁️", "夸克"), "115": ("🟣", "115")}
FAILED_STATUS = {"scrape_failed", "rename_failed"}


def watch_and_spawn(ctx: dict) -> None:
    """QMS 刮削触发成功后调用。推送未启用时直接返回，否则后台守护。

    ctx: {drive, task, names, qms_ok: bool, source: str,
          run_id?: int, strm_plan?: {strm_id, delay} | None, strm_ok?: bool}
    - names：转存的文件名（QMS 记录的 file_name 与之对应）
    - qms_ok：QMS 触发结果。strm_plan（自动转存）= STRM 后台触发计划，推送线程会等
      run_watch 登记的真实触发结果再发（信息条显示「STRM 已生成」）；
      strm_ok（manual 同步触发）为旧通道，直接进信息条
    - source：search / auto，决定走「搜索转存」还是「自动转存」推送开关
    """
    cfg = get_group("settings")["notify"]
    if not cfg.get("enabled") or not cfg.get("sendkey"):
        return
    flag = "on_auto" if ctx.get("source") == "auto" else "on_search"
    if not cfg.get(flag, True):
        return
    if not ctx.get("names"):
        return
    threading.Thread(target=_watch, args=(ctx,), daemon=True).start()


def _watch(ctx: dict) -> None:
    records = _wait_records(ctx)
    strm_res = _wait_strm(ctx)  # 自动转存：等后台线程触发完 STRM，信息条才能如实显示
    header = _header(ctx, records, strm_res)


def _wait_strm(ctx: dict) -> dict | None:
    """等 STRM 触发结果（run_watch 后台线程完成时登记）。没配 STRM（strm_plan=None）→ None。

    超时上限 = delay + run_watch 的刮削确认超时(300s) + 缓冲：STRM 触发在「确认刮完 + delay」后，
    而确认可能比 QMS 记录终态晚（10s 轮询间隔）。
    """
    plan = ctx.get("strm_plan")
    if not plan:
        return None
    from ..models import Record, RunHistory
    from . import run_watch  # 延迟导入避免循环

    tbl = Record if ctx.get("strm_table") == "Record" else RunHistory
    deadline = time.time() + int(plan.get("delay", 10)) + 340
    while time.time() < deadline:
        r = run_watch.get_strm_result(ctx.get("run_id"), tbl)
        if r is not None:
            return r
        time.sleep(3)
    return None
    renamed = [r for r in records if r.get("status") == "renamed" and r.get("tmdb_id")]
    if renamed:
        body, title = _build(renamed)
        if body:
            notify.push(title, f"{header}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done", short="简介")
            return
    _fallback(ctx, records, strm_res)


def _wait_records(ctx: dict) -> list[dict]:
    """轮询 QMS 刮削记录，直到转存文件全部到达终态（renamed/失败态）或超时。

    返回匹配到的记录：失败记录一并返回（信息条展示失败数，正文只用 renamed）。
    """
    deadline = time.time() + POLL_TIMEOUT
    names = set(ctx["names"])
    while True:
        # ⚠️ 不按 name= 筛：QMS 的 name 过滤实测会漏记录（2026-10-04：41.4k.mp4 有 scrape_failed
        # 记录，带 name 查却查不到）——一旦漏掉就会白等 30 分钟后退化成无图纯文字推送。
        # 改拉最新 500 条按 file_name 精确匹配（记录新→旧，刚转存的必在前排）。
        rows = qms.scrape_records(page_size=500) or []
        matched = [r for r in rows if r.get("file_name") in names]
        hit = {r.get("file_name") for r in matched}
        missing = names - hit
        alive = [r for r in matched if r.get("status") not in IGNORE_STATUS]
        arrived = bool(alive) and all(r.get("status") in TERMINAL_STATUS for r in alive)
        if arrived or (not missing and not alive):
            # 全部就绪（含失败态）；或 QMS 里查到的都是 ignore（等于没得等了）
            return matched
        if not missing and matched and time.time() > deadline:
            return matched
        if time.time() > deadline:
            print(f"[push] 等 QMS 刮削超时：{len(names) - len(missing)}/{len(names)} 个文件有记录")
            return matched
        time.sleep(POLL_INTERVAL)


def _header(ctx: dict, records: list[dict], strm_res: dict | None = None) -> str:
    """信息条：网盘 · 任务名 + 整单状态徽章 / 转存集数 / QMS 结果 / STRM 结果（带图标）。"""
    icon, drive_name = DRIVE_META.get(ctx.get("drive"), ("📁", ctx.get("drive", "")))
    st_icon, st_text = _overall_status(ctx, records, strm_res)
    lines = [f"{icon} **{drive_name} · {ctx.get('task', '')}** {st_icon} {st_text}"]
    stats: list[str] = []
    is_tv = any(r.get("type") == "tvshow" for r in records) if records else False
    unit = "集" if is_tv else "个文件"
    stats.append(f"📦 转存 {len(ctx['names'])} {unit}")
    if ctx.get("qms_ok") is False:
        stats.append("❌ QMS 触发失败")
    elif ctx.get("qms_ok"):
        if records:
            failed = [r for r in records if r.get("status") in FAILED_STATUS]
            if not failed:
                stats.append("✅ QMS 刮削成功")
            elif any(r.get("status") == "renamed" for r in records):
                stats.append(f"⚠️ QMS 失败 {len(failed)}/{len(records)}")
            else:
                stats.append("❌ QMS 刮削失败")
        else:
            stats.append("⚠️ QMS 无记录")
    # STRM：自动转存走 strm_res（run_watch 后台线程的真实触发结果）；manual 同步触发走 strm_ok 旧通道
    if strm_res is not None:
        if strm_res.get("cls") == "t-bad":
            stats.append(f"❌ STRM {strm_res.get('st', '触发失败')}")
        elif strm_res.get("cls") == "t-ok":
            stats.append("✅ STRM 已生成")
        else:
            stats.append(f"⏳ STRM {strm_res.get('st', '状态未知')}")
    elif ctx.get("strm_ok") is True:
        stats.append("✅ STRM 生成")
    elif ctx.get("strm_ok") is False:
        stats.append("❌ STRM 触发失败")
    elif ctx.get("strm_plan"):
        stats.append("⏳ STRM 状态未知")
    lines.append("")
    lines.append(" · ".join(stats))
    return "\n".join(lines)


def _overall_status(ctx: dict, records: list[dict], strm_res: dict | None = None) -> tuple[str, str]:
    """整单状态徽章：一眼看出这个定时任务跑得怎么样。

    ✅ 成功 = 转存全成且刮削/STRM 无失败；🟡 部分成功 = 好的居多；
    🟠 部分失败 = 坏的居多；❌ 失败 = 全军覆没（含 QMS 触发失败）。
    """
    if ctx.get("qms_ok") is False:
        return "❌", "失败"
    ok = sum(1 for r in records if r.get("status") == "renamed")
    fail = sum(1 for r in records if r.get("status") in FAILED_STATUS)
    if ctx.get("strm_ok") is False or (strm_res is not None and strm_res.get("cls") == "t-bad"):
        fail += 1
    if not records and ctx.get("qms_ok") is not True:
        return "✅", "成功"
    if fail == 0:
        return "✅", "成功"
    if ok == 0 and fail > 0:
        return "❌", "失败"
    return ("🟡", "部分成功") if ok > fail else ("🟠", "部分失败")


def _build(records: list[dict]) -> tuple[str, str]:
    """renamed 记录 → (正文, title)。按剧/电影分组，每组一块；多组拼进同一条消息。"""
    groups: dict[int, list[dict]] = {}
    for r in records:
        groups.setdefault(int(r.get("tmdb_id") or 0), []).append(r)
    if not groups or 0 in groups:
        return "", ""

    blocks: list[str] = []
    main_title = ""
    is_tv = False
    total_eps = 0
    for tmdb_id, items in groups.items():
        first = items[0]
        media_name = first.get("media_name") or first.get("file_name") or "未知"
        if first.get("type") == "tvshow":
            is_tv = True
            total_eps += len(items)
            block, name = _tv_block(tmdb_id, media_name, items)
        else:
            block, name = _movie_block(tmdb_id, media_name, items)
        if not block:
            return "", ""
        if not main_title:
            main_title = name
        blocks.append(block)

    title = f"{main_title} · 更新 {total_eps} 集" if is_tv else main_title
    return "\n\n".join(blocks), title


def _tv_block(tmdb_id: int, media_name: str, items: list[dict]) -> tuple[str, str]:
    detail = tmdb.tv_detail(tmdb_id)
    if detail is None:
        return "", ""
    backdrop = tmdb.img_url(detail.get("backdrop_path")) or tmdb.img_url(detail.get("poster_path"))
    season = int(items[0].get("season_number") or 1)
    eps = tmdb.tv_season(tmdb_id, season) or []
    eps_map = {int(e.get("episode_number") or 0): e for e in eps}

    lines = [f"## {media_name}", "", f"![{media_name}]({backdrop})", ""]
    ordered = sorted(items, key=lambda r: int(r.get("episode_number") or 0))
    for r in ordered:
        ep_no = int(r.get("episode_number") or 0)
        ep = eps_map.get(ep_no) or {}
        ep_name = ep.get("name") or f"第 {ep_no} 集"
        overview = (ep.get("overview") or "").strip()
        still = tmdb.img_url(ep.get("still_path"))
        lines += [
            f"### **集：S{season:02d}E{ep_no:02d} - {ep_name}**",
            "",
            f"![S{season:02d}E{ep_no:02d}]({still})" if still else "",
            "",
            f"**简介：{_clip(overview)}**",
            "", "", "", "",  # 集间留白 3 空行（规范 §3）
        ]
    return "\n".join(lines).rstrip() + "\n", media_name


def _movie_block(tmdb_id: int, media_name: str, items: list[dict]) -> tuple[str, str]:
    detail = tmdb.movie_detail(tmdb_id)
    if detail is None:
        return "", ""
    backdrop = tmdb.img_url(detail.get("backdrop_path")) or tmdb.img_url(detail.get("poster_path"))
    overview = (detail.get("overview") or "").strip()
    year = str(detail.get("release_date") or "")[:4]
    head = f"{year} · " if year else ""
    lines = [
        f"## {media_name}",
        "",
        f"![{media_name}]({backdrop})" if backdrop else "",
        "",
        f"**简介：{head}{_clip(overview)}**",
    ]
    return "\n".join(lines), media_name


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text[:OVERVIEW_MAX] + "…" if len(text) > OVERVIEW_MAX else text


def _fallback(ctx: dict, records: list[dict], strm_res: dict | None = None) -> None:
    """富文本拿不到时的兜底：信息条 + 文件清单（失败的标注原因），通知不丢。"""
    print("[push] 富文本推送回退纯文字")
    failed = {r.get("file_name"): r.get("failed_reason") or "刮削失败" for r in records if r.get("status") in FAILED_STATUS}
    lines = []
    for n in ctx["names"]:
        mark = f"（{failed[n]}）" if n in failed else ""
        lines.append(f"- {n}{mark}")
    body = "\n".join(lines)
    notify.push(f"{ctx.get('task', '转存')} · 转存完成", f"{_header(ctx, records, strm_res)}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done")
