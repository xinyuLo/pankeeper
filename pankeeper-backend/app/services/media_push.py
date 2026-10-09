from __future__ import annotations

import re
import threading
import time

from . import notify, tmdb
from ..adapters.base import is_video_file
from .settings_svc import get_group

OVERVIEW_MAX = 200

TERMINAL_STATUS = {"renamed", "scrape_failed", "rename_failed"}
IGNORE_STATUS = {"ignore"}

DRIVE_META = {"baidu": ("🔵", "百度"), "quark": ("☁️", "夸克"), "115": ("🟣", "115")}
FAILED_STATUS = {"scrape_failed", "rename_failed"}

def watch_and_spawn(ctx: dict) -> None:
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
    from . import media_recognize
    from . import tmdb

    names = [n for n in ctx.get("names", []) if n]
    task = (ctx.get("task") or "").strip()
    icon, drive_name = DRIVE_META.get(ctx.get("drive"), ("📁", ctx.get("drive", "")))
    header = f"{icon} **{drive_name} · {task}** ✅ 成功\n\n📦 转存 {len(names)} 个文件"

    res = media_recognize.recognize_candidates(task or (names[0] if names else ""), file_names=names)
    hit = res.get("best") if res.get("ok") else None

    file_lines = [f"- {n}" for n in names[:15]]
    if len(names) > 15:
        file_lines.append(f"- …等共 {len(names)} 个")

    if hit and hit.get("tmdb_id"):
        media_name = hit.get("title") or "未知影片"

        detail = None
        try:
            detail = tmdb.tv_detail(hit["tmdb_id"]) if hit.get("media_type") == "tv" else tmdb.movie_detail(hit["tmdb_id"])
        except Exception:
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

    body = "\n".join([header, "", "**未识别到影片条目，转存文件清单：**", *file_lines])
    notify.push(f"{task or '转存'} · 转存完成", body, kind=f"{ctx.get('source', 'search')}_done")

def _watch(ctx: dict) -> None:
    try:
        records = _wait_records(ctx)
        strm_res = _wait_strm(ctx)
        header = _header(ctx, records, strm_res)
        renamed = [r for r in records if r.get("status") == "renamed" and r.get("tmdb_id")]
        if renamed:
            body, title = _build(renamed)
            if body:
                notify.push(title, f"{header}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done", short="简介")
                return
        _fallback(ctx, records, strm_res)
    except Exception:

        import traceback

        print("[push] 推送线程异常：\n" + traceback.format_exc(), flush=True)

def _wait_strm(ctx: dict) -> dict | None:
    plan = ctx.get("strm_plan")
    if not plan:
        return None
    from ..models import Record, RunHistory
    from . import run_watch

    tbl = Record if ctx.get("strm_table") == "Record" else RunHistory
    deadline = time.time() + int(plan.get("delay", 10)) + 340
    while time.time() < deadline:
        r = run_watch.get_strm_result(ctx.get("run_id"), tbl)
        if r is not None:
            return r
        time.sleep(3)
    return None

def _wait_records(ctx: dict) -> list[dict]:
    from . import run_watch

    recs, _verdict = run_watch._wait(list(ctx["names"]), ctx.get("qms_baseline") or {})
    return recs

def _header(ctx: dict, records: list[dict], strm_res: dict | None = None) -> str:
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

        from . import run_watch

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
            "", "", "", "",
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
    from . import run_watch

    print("[push] 富文本推送回退纯文字")
    failed = {r.get("file_name"): r.get("failed_reason") or "刮削失败" for r in records if r.get("status") in FAILED_STATUS}
    seen = {r.get("file_name") for r in records}
    lines = []
    for n in ctx["names"]:
        if n in failed:
            lines.append(f"- {n}（{failed[n]}）")
        elif n not in seen and is_video_file(n):
            lines.append(f"- {n}（未见 QMS 刮削记录）")
        else:
            lines.append(f"- {n}")
    body = "\n".join(lines)
    notify.push(f"{ctx.get('task', '转存')} · 转存完成", f"{_header(ctx, records, strm_res)}\n\n{body}", kind=f"{ctx.get('source', 'search')}_done")
