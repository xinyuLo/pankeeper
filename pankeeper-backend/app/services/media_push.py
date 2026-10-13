"""富文本推送（路线 A 联动）：等 QMS 刮削完成后查记录 + TMDB 拼 Markdown 推送。

流程：转存成功触发 QMS 刮削 → engine 调 watch_and_spawn → 本模块后台轮询刮削记录，
全部到达终态（renamed 或 失败态）后拼 desp 推送。desp 顶部是 PanKeeper 自定义的
信息条（网盘 · 任务名 / 转存集数 / QMS 结果 / STRM 结果），正文按
《Server酱推送格式规范》拼（多集合并一条，w500 图，h2 剧名 + h3 集行）。

兜底：QMS 超时 / 无可用记录 / TMDB 拿不到图 → 回退纯文字推送（带信息条+文件清单）；
失败通知仍走原通道（不带图）。
"""
from __future__ import annotations

import random
import re
import threading
import time

from . import notify, tmdb
from ..adapters.base import is_video_file
from .settings_svc import get_group

# 轮询节奏/超时统一由 run_watch._wait 管（媒体推送与结果回填共用一套口径，别再各写一份）
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
    if not cfg.get("enabled") or not notify.channel_ready(cfg):
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
            f"⁠　**简介：{_clip(overview)}**" if overview else "",
            "",
            f"**本次转存 {len(names)} 个文件：**",
            *file_lines,
        ])
        notify.push(name_line, f"{header}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done",
                    short=_clip(overview) if overview else None,
                    title_bark=f"{icon} {drive_name} · {name_line} ✅ 成功",
                    markdown=f"📦 {len(names)} 个文件\n\n{_bark_md(body)}",
                    cover=cover)
        return

    # 识别不到 → 纯文字（文件清单 + 如实说明），通知不丢
    list_text = "\n".join(["**未识别到影片条目，转存文件清单：**", *file_lines])
    body = "\n".join([header, "", list_text])
    notify.push(f"{task or '转存'} · 转存完成", body, kind=f"{ctx.get('source', 'search')}_done",
                title_bark=f"{icon} {drive_name} · {task or '转存'} ✅ 成功",
                markdown=f"📦 {len(names)} 个文件\n\n{list_text}")


def _watch(ctx: dict) -> None:
    try:
        # QMS 触发失败（转存成功但联动没跑起来）：没有刮削可等，立即如实推失败，
        # 不进 30 分钟记录轮询（2026-10-13 清单四：此前这条路上全程无推送）
        if ctx.get("qms_ok") is False:
            _qms_trigger_failed(ctx)
            return
        records = _wait_records(ctx)
        strm_res = _wait_strm(ctx)  # 自动转存：等后台线程触发完 STRM，信息条才能如实显示
        emby = (strm_res or {}).get("emby") or None  # Bark/Server酱 统计行展示「触发 Emby 库刷新」
        header = _header(ctx, records, strm_res, emby)
        renamed = [r for r in records if r.get("status") == "renamed" and r.get("tmdb_id")]
        if renamed:
            body, title, cover = _build(renamed)
            if body:
                # Bark 标题=信息条首行（☁️ 夸克 · 影片名 ✅ 成功），不传副标题；
                # markdown=统计行+富文本去图（App 内渲染格式，横幅降级纯文本）；
                # cover=横版封面（backdrop 优先；Bark 单图走它，显式传防正文顺序变化）
                notify.push(title, f"{header}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done",
                            title_bark=_bark_title(ctx, records, strm_res),
                            markdown=_bark_markdown(ctx, records, strm_res, body, emby),
                            cover=cover)
                return
        _fallback(ctx, records, strm_res, emby)
    except Exception:
        # 守护线程死了要留痕——2026-10-04 实锤：推送尾巴曾被错位成死代码，整个链路
        # 无声无息不推送也不报错，靠 push_logs 缺行才定位到
        import traceback

        print("[push] 推送线程异常：\n" + traceback.format_exc(), flush=True)

def _qms_trigger_failed(ctx: dict) -> None:
    """QMS 触发失败的即时推送：转存成功但联动没跑起来，信息条 + 文件清单，不等记录（没得等）。"""
    names = [n for n in ctx.get("names", []) if n]
    header = _header(ctx, [], None, None)
    file_lines = [f"- {n}" for n in names[:15]]
    if len(names) > 15:
        file_lines.append(f"- …等共 {len(names)} 个")
    note = "**QMS 触发失败，刮削/STRM 均未执行。转存文件：**"
    body = f"{header}\n\n{note}\n" + "\n".join(file_lines)
    notify.push(f"{ctx.get('task', '转存')} · QMS 触发失败", body,
                kind=f"{ctx.get('source', 'search')}_done",
                title_bark=_bark_title(ctx, [], None),
                markdown=_bark_markdown(ctx, [], None, note + "\n" + "\n".join(file_lines)))


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


def _header(ctx: dict, records: list[dict], strm_res: dict | None = None, emby: str | None = None) -> str:
    """信息条：网盘 · 任务名 + 整单状态徽章 / 转存集数 / QMS 结果 / STRM 结果（带图标）。

    emby 给了就单独一行「🎬 触发 Emby「库」库刷新」（2026-10-13 用户定案：不并进 · 分隔长行）。"""
    icon, drive_name = DRIVE_META.get(ctx.get("drive"), ("📁", ctx.get("drive", "")))
    st_icon, st_text = _overall_status(ctx, records, strm_res)
    stats = " · ".join(_stats_list(ctx, records, strm_res))
    if emby:
        stats += f"\n\n🎬 触发 Emby「{emby}」库刷新"
    lines = [f"{icon} **{drive_name} · {ctx.get('task', '')}** {st_icon} {st_text}", "", stats]
    return "\n".join(lines)


def _bark_title(ctx: dict, records: list[dict], strm_res: dict | None = None) -> str:
    """Bark 标题：信息条首行拍平——「☁️ 夸克 · 冰川时代 (2002) ✅ 成功」（2026-10-13 用户定案）。

    与 Server酱 信息条首行同款（网盘 · 影片名 ✅ 整单状态）；副标题不传；
    统计段（转存数/QMS/STRM）放正文开头（_bark_markdown）。"""
    icon, drive_name = DRIVE_META.get(ctx.get("drive"), ("📁", ctx.get("drive", "")))
    st_icon, st_text = _overall_status(ctx, records, strm_res)
    task = (ctx.get("task") or "").strip()
    return f"{icon} {drive_name} · {task} {st_icon} {st_text}"


# markdown 参数里的图片会降级成裸链接文本（海报已走 image 参数），整段剥掉
_MD_IMG_LINE = re.compile(r"!\[[^\]]*\]\([^)]+\)\s*")


def _bark_md(body_md: str) -> str:
    """富文本 → Bark markdown 正文：去图（图走 image 参数）+ 片名标题升一级（## → #，App 里更大）。

    Server酱 desp 保持 ## 不动，只在 Bark 这条路上升级。"""
    md = _MD_IMG_LINE.sub("", body_md).strip()
    return re.sub(r"(?m)^## ", "# ", md)


def _bark_markdown(ctx: dict, records: list[dict], strm_res: dict | None, body_md: str, emby: str | None = None) -> str:
    """Bark markdown 正文（2026-10-13 用户定案）：统计行 + 富文本去图。

    App 历史里渲染加粗/标题/列表（横幅降级纯文本）；图片不进 markdown（降级成裸链接），
    走 image 参数展示。emby 给了就统计行下面单独一行「🎬 触发 Emby「库」库刷新」。"""
    stats = " · ".join(_stats_list(ctx, records, strm_res))
    if emby:
        stats += f"\n\n🎬 触发 Emby「{emby}」库刷新"
    md = _bark_md(body_md)
    return f"{stats}\n\n{md}" if md else stats


def _stats_list(ctx: dict, records: list[dict], strm_res: dict | None = None) -> list[str]:
    """信息条统计段（转存数 / QMS 结果 / STRM 结果），Server酱 信息条与 Bark 正文共用。

    口径（2026-10-13 定案）：完整文字不压缩（✅ QMS 刮削成功 / ✅ STRM 已生成）；
    带原因/数量注释的条目（⚠️ 部分失败 / 未见记录 / t-bad 的失败原因）原样保留；
    没等到 STRM 返回值一律按 ❌ 标（2026-10-12 用户定案）。
    Emby 段不在这里——由 _header/_bark_markdown 单独一行拼（2026-10-13 用户定案）。"""
    stats: list[str] = []
    is_tv = any(r.get("type") == "tvshow" for r in records) if records else False
    unit = "集" if is_tv else "个文件"
    stats.append(f"📦 转存 {len(ctx['names'])} {unit}")
    if ctx.get("qms_ok") is False:
        stats.append("❌ QMS 触发失败")
        stats.append("❌ STRM 未触发")
        return stats
    if ctx.get("qms_ok"):
        # 未见记录的文件如实点名（2026-10-06）；「未见」只数视频文件——nfo/图片等杂件
        # QMS 过滤不刮、永远没有记录，不能算未见（2026-10-07 实锤：电影夹带 nfo/图片
        # 被算成未见 → 卡等待 30 分钟 → Server酱静默）
        from . import run_watch  # 延迟导入避免循环

        missing = len(run_watch.video_names(ctx["names"])) - len({r.get("file_name") for r in records})
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
    # STRM：自动转存走 strm_res（run_watch 后台线程的真实触发结果）；manual 同步触发走 strm_ok 旧通道。
    # t-bad/t-off 的 st 带具体原因，属注释保留
    if strm_res is not None:
        if strm_res.get("cls") == "t-ok":
            stats.append("✅ STRM 已生成")
        elif strm_res.get("cls") == "t-bad":
            stats.append(f"❌ STRM {strm_res.get('st', '触发失败')}")
        else:
            stats.append(f"❌ STRM {strm_res.get('st', '状态未知')}")
    elif ctx.get("strm_ok") is True:
        stats.append("✅ STRM 已生成")
    else:
        stats.append("❌ STRM 未返回")
    return stats


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


def _build(records: list[dict]) -> tuple[str, str, str]:
    """renamed 记录 → (正文, title, cover)。按剧/电影分组，每组一块；多组拼进同一条消息。

    cover = 第一组的**横版封面**（backdrop 优先，poster 兜底，与正文首图同款但显式传递，
    不再依赖正文顺序抓取）；拿不到返回空串（Bark 侧退回正文首图提取）。"""
    groups: dict[int, list[dict]] = {}
    for r in records:
        groups.setdefault(int(r.get("tmdb_id") or 0), []).append(r)
    if not groups or 0 in groups:
        return "", "", ""

    blocks: list[str] = []
    main_title = ""
    cover = ""
    is_tv = False
    total_eps = 0
    for tmdb_id, items in groups.items():
        first = items[0]
        media_name = first.get("media_name") or first.get("file_name") or "未知"
        if first.get("type") == "tvshow":
            is_tv = True
            total_eps += len(items)
            block, name, poster = _tv_block(tmdb_id, media_name, items)
        else:
            block, name, poster = _movie_block(tmdb_id, media_name, items)
        if not block:
            return "", "", ""
        if not main_title:
            main_title = name
        if not cover:
            cover = poster
        blocks.append(block)

    title = f"{main_title} · 更新 {total_eps} 集" if is_tv else main_title
    return "\n\n".join(blocks), title, cover


# 电视剧简介两档阈值（2026-10-13 用户定案）：集数超过该值、或逐集简介合计超过字数预算时，
# 切紧凑模式——只发整体剧简介 + 集行（不逐集简介）。预算按 Bark 载荷反推：markdown 参数
# 走 APNs 4KB 上限，中文 3 字节/字，标题/统计/URL/参数已占 ~1KB，正文留 ~1000 字。
TV_COMPACT_MAX_EPS = 5
TV_COMPACT_OVERVIEW_CHARS = 700


def _tv_block(tmdb_id: int, media_name: str, items: list[dict]) -> tuple[str, str, str]:
    detail = tmdb.tv_detail(tmdb_id)
    if detail is None:
        return "", "", ""
    backdrop = tmdb.img_url(detail.get("backdrop_path")) or tmdb.img_url(detail.get("poster_path"))
    season = int(items[0].get("season_number") or 1)
    eps = tmdb.tv_season(tmdb_id, season) or []
    eps_map = {int(e.get("episode_number") or 0): e for e in eps}

    ordered = sorted(items, key=lambda r: int(r.get("episode_number") or 0))
    ep_rows = []
    for r in ordered:
        ep_no = int(r.get("episode_number") or 0)
        ep = eps_map.get(ep_no) or {}
        ep_rows.append((ep_no, ep.get("name") or f"第 {ep_no} 集", (ep.get("overview") or "").strip(), tmdb.img_url(ep.get("still_path"))))

    # 紧凑模式（集数多 / 逐集简介超量）：整体剧简介 + 集行，不逐集简介
    show_overview = (detail.get("overview") or "").strip()
    compact = len(ep_rows) > TV_COMPACT_MAX_EPS or sum(len(o) for _, _, o, _ in ep_rows) > TV_COMPACT_OVERVIEW_CHARS

    # Bark 横版封面：backdrop 优先；短剧常没配 backdrop → **随机挑一张剧照**（也是横版）；
    # 再没有退回竖版 poster（2026-10-13 用户定案）
    stills = [s for _, _, _, s in ep_rows if s]
    cover = backdrop or (random.choice(stills) if stills else "") or tmdb.img_url(detail.get("poster_path")) or ""

    lines = [f"## {media_name}", "", f"![{media_name}]({backdrop})" if backdrop else "", ""]
    if compact:
        lines += [f"⁠　**简介：{_clip(show_overview)}**" if show_overview else "", ""]
    for ep_no, ep_name, overview, still in ep_rows:
        lines += [
            f"### **集：S{season:02d}E{ep_no:02d} - {ep_name}**",
            "",
            f"![S{season:02d}E{ep_no:02d}]({still})" if still else "",
            "",
        ]
        if not compact:
            lines += [f"⁠　**简介：{_clip(overview)}**", ""]
        lines += ["", "", ""]  # 集间留白 3 空行（规范 §3）
    return "\n".join(lines).rstrip() + "\n", media_name, cover


def _movie_block(tmdb_id: int, media_name: str, items: list[dict]) -> tuple[str, str, str]:
    detail = tmdb.movie_detail(tmdb_id)
    if detail is None:
        return "", "", ""
    backdrop = tmdb.img_url(detail.get("backdrop_path")) or tmdb.img_url(detail.get("poster_path"))
    overview = (detail.get("overview") or "").strip()
    year = str(detail.get("release_date") or "")[:4]
    head = f"{year} · " if year else ""
    lines = [
        f"## {media_name}",
        "",
        f"![{media_name}]({backdrop})" if backdrop else "",
        "",
        f"⁠　**简介：{head}{_clip(overview)}**",
    ]
    return "\n".join(lines), media_name, backdrop or ""


def _clip(text: str) -> str:
    """简介清洗：压平空白，全量发送不截断（2026-10-13 用户定案：直接发全）。

    原 200 字截断随 Bark 通道一起废除——Server酱 desp 与 Bark markdown 都装得下整段简介。"""
    return re.sub(r"\s+", " ", text).strip()


def _fallback(ctx: dict, records: list[dict], strm_res: dict | None = None, emby: str | None = None) -> None:
    """富文本拿不到时的兜底：信息条 + 文件清单（失败/未见记录的标注原因），通知不丢。"""
    from . import run_watch  # 延迟导入避免循环

    print("[push] 富文本推送回退纯文字")
    failed = {r.get("file_name"): r.get("failed_reason") or "刮削失败" for r in records if r.get("status") in FAILED_STATUS}
    seen = {r.get("file_name") for r in records}
    lines = []
    for n in ctx["names"]:
        if n in failed:
            lines.append(f"- {n}（{failed[n]}）")
        elif n not in seen and is_video_file(n):  # 杂件（nfo/图片）QMS 不刮，不标未见
            lines.append(f"- {n}（未见 QMS 刮削记录）")
        else:
            lines.append(f"- {n}")
    body = "\n".join(lines)
    notify.push(f"{ctx.get('task', '转存')} · 转存完成", f"{_header(ctx, records, strm_res, emby)}\n\n{body}",
                kind=f"{ctx.get('source', 'search')}_done", title_bark=_bark_title(ctx, records, strm_res),
                markdown=_bark_markdown(ctx, records, strm_res, body, emby))
