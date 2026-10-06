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

from . import notify, tmdb
from .settings_svc import get_group

# 轮询节奏/超时统一由 run_watch._wait 管（媒体推送与结果回填共用一套口径，别再各写一份）
OVERVIEW_MAX = 200        # 简介截断长度（规范 §6.3）

# qMediaSync 记录的终态：renamed = 整理完成；两种 failed 也算"到了"（失败信息进信息条）
TERMINAL_STATUS = {"renamed", "scrape_failed", "rename_failed"}
IGNORE_STATUS = {"ignore"}   # 不会到任何终态，等待时剔除

DRIVE_META = {"baidu": ("🔵", "百度"), "quark": ("☁️", "夸克"), "115": ("🟣", "115")}
FAILED_STATUS = {"scrape_failed", "rename_failed"}


def watch_and_spawn(ctx: dict) -> None:
    """推送总入口：按联动后端分流成**两套互不掺杂的日志/推送流程**（用户 2026-10-04 要求）。

    - backend=qms（默认）：转存完成消息推送走 QMS 流程——等 QMS 刮削终态（_wait_records）→
      拿记录里的 tmdb_id 拼 TMDB 富文本 → 等 STRM 结果（_wait_strm）→ 推送。
    - backend=litepan：LitePan 状态对外不可见（只有 webhook 进、没有查询出），推送走
      **自识别流程**（_watch_own）——PanKeeper 自己从文件名识别 TMDB 直接推送，不等不查。

    ctx: {drive, task, names, source, backend?: 'qms'|'litepan',
          run_id?/strm_plan?/strm_ok?}（后三者为 qms 流程专用，见 _wait_strm/_header）
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
    backend = ctx.get("backend") or get_group("media").get("backend", "qms")
    target = _watch_own if backend != "qms" else _watch
    threading.Thread(target=target, args=(ctx,), daemon=True).start()


def _watch_own(ctx: dict) -> None:
    """LitePan 模式的**独立推送流程**：后端刮削状态不可见（只有 webhook 进、没有查询出），
    PanKeeper 自己识别 TMDB 直接推送——不等、不查、不假装。

    **按转存文件夹识别一次**（2026-10-06 用户定稿）：识别到了推那一部影片的富文本
    （海报+简介+文件清单）；识别不到推纯文字文件清单。不再逐文件识别——一个分享里
    正片+花絮+多版本各自命中不同 TMDB 条目，会把推送撑成一大坨杂烩（哪吒 12 文件实锤）。
    全程不出现 QMS 字样（两套日志流程分开）。"""
    from . import media_recognize
    from . import tmdb

    names = [n for n in ctx.get("names", []) if n]
    task = (ctx.get("task") or "").strip()
    icon, drive_name = DRIVE_META.get(ctx.get("drive"), ("📁", ctx.get("drive", "")))
    header = f"{icon} **{drive_name} · {task}** ✅ 成功\n\n📦 转存 {len(names)} 个文件"

    # 文件夹名识别一次（recognize_candidates：movie+tv 双搜打分，best 即最可信条目；
    # 内部自带 30 分钟缓存）。文件名集合作为消歧信号传入
    res = media_recognize.recognize_candidates(task or (names[0] if names else ""), file_names=names)
    hit = res.get("best") if res.get("ok") else None

    file_lines = [f"- {n}" for n in names[:15]]
    if len(names) > 15:
        file_lines.append(f"- …等共 {len(names)} 个")

    if hit and hit.get("tmdb_id"):
        media_name = hit.get("title") or "未知影片"
        # 候选里的简介只截了 120 字：用 tmdb_id 补拉详情拿完整简介 + 背景图
        detail = None
        try:
            detail = tmdb.tv_detail(hit["tmdb_id"]) if hit.get("media_type") == "tv" else tmdb.movie_detail(hit["tmdb_id"])
        except Exception:  # noqa: BLE001 —— 补拉失败就用候选里的截断简介
            detail = None
        overview = ((detail or {}).get("overview") or hit.get("overview") or "").strip()
        cover = tmdb.img_url((detail or {}).get("backdrop_path")) or hit.get("poster")
        year = hit.get("year")
        name_line = f"{media_name} ({year})" if year else media_name
        body = "\n".join([
            f"## {name_line}",
            "",
            f"![{name_line}]({cover})" if cover else "",
            "",
            f"**简介：{_clip(overview)}**" if overview else "",
            "",
            f"**本次转存 {len(names)} 个文件：**",
            *file_lines,
        ])
        notify.push(name_line, f"{header}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done", short="简介")
        return

    # 识别不到 → 纯文字（文件清单 + 如实说明），通知不丢
    body = "\n".join([header, "", "**未识别到影片条目，转存文件清单：**", *file_lines])
    notify.push(f"{task or '转存'} · 转存完成", body, kind=f"{ctx.get('source', 'search')}_done")


def _watch(ctx: dict) -> None:
    try:
        records = _wait_records(ctx)
        strm_res = _wait_strm(ctx)  # 自动转存：等后台线程触发完 STRM，信息条才能如实显示
        header = _header(ctx, records, strm_res)
        renamed = [r for r in records if r.get("status") == "renamed" and r.get("tmdb_id")]
        if renamed:
            body, title = _build(renamed)
            if body:
                notify.push(title, f"{header}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done", short="简介")
                return
        _fallback(ctx, records, strm_res)
    except Exception:
        # 守护线程死了要留痕——2026-10-04 实锤：推送尾巴曾被错位成死代码，整个链路
        # 无声无息不推送也不报错，靠 push_logs 缺行才定位到
        import traceback

        print("[push] 推送线程异常：\n" + traceback.format_exc(), flush=True)

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


def _wait_records(ctx: dict) -> list[dict]:
    """轮询 QMS 刮削记录，等**本次转存的全部文件**出现本次触发产生的新终态记录。

    2026-10-06 起委托 run_watch._wait（统一两处实现——此前各自维护，同一个早退 bug
    连犯两遍：只看"已匹配记录是否终态"、没管还没出现记录的文件，6 个文件只等到 1 个
    旧记录就推送，标题成了「更新 1 集」）。且**只认本次触发后的新记录**（baseline 指纹，
    ctx.qms_baseline）：旧记录不算这次的账，同名裸名文件跨剧碰撞（狂飙 17.mp4 撞兰香
    如故旧记录）不会再把推送标题变成别的剧。

    返回本次的新记录（失败记录一并返回）；没等到的一律不在其中，由 _header/_fallback 如实标注。"""
    from . import run_watch  # 延迟导入避免循环

    recs, _verdict = run_watch._wait(list(ctx["names"]), ctx.get("qms_baseline") or {})
    return recs


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
        # 未见记录的文件如实点名（2026-10-06：旧版只统计匹配到的记录，缺文件毫无声息）
        missing = len(ctx["names"]) - len({r.get("file_name") for r in records})
        if records:
            failed = [r for r in records if r.get("status") in FAILED_STATUS]
            if not failed:
                stats.append("✅ QMS 刮削成功" if not missing else f"✅ QMS 刮削成功（⚠️ {missing} 项未见记录）")
            elif any(r.get("status") == "renamed" for r in records):
                stats.append(f"⚠️ QMS 失败 {len(failed)}/{len(records)}")
            else:
                stats.append("❌ QMS 刮削失败")
        elif missing:
            stats.append(f"⚠️ QMS {missing} 项未见记录")
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
    """富文本拿不到时的兜底：信息条 + 文件清单（失败/未见记录的标注原因），通知不丢。"""
    print("[push] 富文本推送回退纯文字")
    failed = {r.get("file_name"): r.get("failed_reason") or "刮削失败" for r in records if r.get("status") in FAILED_STATUS}
    seen = {r.get("file_name") for r in records}
    lines = []
    for n in ctx["names"]:
        if n in failed:
            lines.append(f"- {n}（{failed[n]}）")
        elif n not in seen:
            lines.append(f"- {n}（未见 QMS 刮削记录）")
        else:
            lines.append(f"- {n}")
    body = "\n".join(lines)
    notify.push(f"{ctx.get('task', '转存')} · 转存完成", f"{_header(ctx, records, strm_res)}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done")
