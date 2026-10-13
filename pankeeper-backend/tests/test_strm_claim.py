"""STRM 触发的刮削完成判据：记录池认领（2026-10-13 快刮竞态修复）回归测试。

背景：刮削在首个轮询间隔（10s）内跑完、且 updated_at 不前进时，路径状态判据的
两条证据都会缺席，旧实现只能白等 300s 超时、STRM 永不触发（宁缺勿错 + 自愈已砍）。
新判据按原始路径认领本次记录（claim_records），全部终态即视为刮完。

monkeypatch run_watch.qms 全家 + time.sleep，不碰网络、真实账号与开发库。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.run_watch as rw  # noqa: E402

CLAIM_DIR = "/kuake/1.影视/待整理-电影/这个杀手不太冷静 (2022)"
WATCH_ROOT = "/kuake/1.影视/待整理-电影"

# 路径状态假件：永远不忙 + updated_at 停在远古（快刮竞态的两条 (a) 证据同时缺席）
def _stale_status(_pid):
    return {"is_running": 0, "is_scraping": False, "updated_at": 1}


def _rec(rid, fn, status, src_dir=CLAIM_DIR, new_path="华语电影/这个杀手不太冷静 (2022)"):
    return {
        "id": rid,
        "file_name": fn,
        "status": status,
        "path": src_dir,
        "source_full_path": f"{src_dir}/{fn}",
        "new_path": new_path,
    }


def _wait_fired(fired: dict, seconds: float = 5.0) -> None:
    deadline = time.time() + seconds
    while "args" not in fired and time.time() < deadline:
        time.sleep(0.01)


def test_fast_scrape_race_fires_via_records(monkeypatch):
    """竞态主场景：路径状态从不忙 + updated_at 远古，记录池里本次文件全部终态
    → 必须经记录池判据 (b) 触发 STRM，而不是 300s 超时后「未触发」。"""
    old_junk = [_rec(10, "别的剧.mp4", "renamed", src_dir="/kuake/待整理-电视剧/别的剧")]
    mine = [_rec(20, "a.mkv", "renamed"), _rec(21, "b.mkv", "renamed")]
    reads = {"n": 0}

    def fake_records(page_size=1000, page=1, name=None, status=None):
        reads["n"] += 1
        # 第一次读取是 _records_max_id（分界线）：只有旧记录；之后认领时带上本次的
        return list(old_junk) if reads["n"] == 1 else old_junk + mine

    monkeypatch.setattr(rw.qms, "scrape_records", fake_records)
    monkeypatch.setattr(rw.qms, "scrape_path_status", _stale_status)
    monkeypatch.setattr(rw.time, "sleep", lambda s: None)
    monkeypatch.setattr(rw, "_record_names", lambda rid, table: ["a.mkv", "b.mkv"])
    fired: dict = {}
    monkeypatch.setattr(
        rw, "_fire_strm",
        lambda strm_id, names, claim_dir="", since_id=0: fired.update(
            args=(strm_id, sorted(names), claim_dir, since_id)) or {"st": "已触发（定向临时任务）", "cls": "t-ok"},
    )

    rw.trigger_strm_after_scrape(None, 8, 8, delay=0, timeout=5, claim_dir=CLAIM_DIR)
    _wait_fired(fired)
    assert fired["args"] == (8, ["a.mkv", "b.mkv"], CLAIM_DIR, 10)


def test_claim_ignores_old_records(monkeypatch, capsys):
    """认领分界：本次文件的同路径旧记录（id ≤ 触发前分界）不算本次——
    旧按名全池匹配会把旧 scrape_failed 冒充成「本次刮削失败」或替本次打卡。"""
    old_mine = [_rec(5, "a.mkv", "scrape_failed")]  # 同路径旧失败记录，id 不大于分界线
    monkeypatch.setattr(rw.qms, "scrape_records", lambda page_size=1000, page=1, name=None, status=None: list(old_mine))
    monkeypatch.setattr(rw.qms, "scrape_path_status", _stale_status)
    monkeypatch.setattr(rw.time, "sleep", lambda s: None)
    monkeypatch.setattr(rw, "_record_names", lambda rid, table: ["a.mkv"])
    fired: dict = {}
    monkeypatch.setattr(
        rw, "_fire_strm",
        lambda strm_id, names, claim_dir="", since_id=0: fired.update(args=1) or {"st": "x", "cls": "t-ok"},
    )

    rw.trigger_strm_after_scrape(None, 8, 8, delay=0, timeout=1, claim_dir=CLAIM_DIR)
    _wait_fired(fired, seconds=4)
    assert "args" not in fired  # 认领不到 → 判据全缺席 → 走超时，宁缺勿错
    assert "不触发 STRM" in capsys.readouterr().out


def test_claimed_failed_blocks_strm(monkeypatch, capsys):
    """认领到的记录里有 scrape_failed → 照旧「失败不生成 STRM」（口径不变，
    但失败判定已被路径认领保护，不会被同名旧失败记录误伤）。"""
    old_junk = [_rec(10, "别的剧.mp4", "renamed", src_dir="/kuake/待整理-电视剧/别的剧")]
    mine = [_rec(20, "a.mkv", "renamed"), _rec(21, "b.mkv", "scrape_failed")]
    reads = {"n": 0}

    def fake_records(page_size=1000, page=1, name=None, status=None):
        reads["n"] += 1
        return list(old_junk) if reads["n"] == 1 else old_junk + mine

    monkeypatch.setattr(rw.qms, "scrape_records", fake_records)
    monkeypatch.setattr(rw.qms, "scrape_path_status", _stale_status)
    monkeypatch.setattr(rw.time, "sleep", lambda s: None)
    monkeypatch.setattr(rw, "_record_names", lambda rid, table: ["a.mkv", "b.mkv"])
    fired: dict = {}
    monkeypatch.setattr(
        rw, "_fire_strm",
        lambda strm_id, names, claim_dir="", since_id=0: fired.update(args=1) or {"st": "x", "cls": "t-ok"},
    )

    rw.trigger_strm_after_scrape(None, 8, 8, delay=0, timeout=5, claim_dir=CLAIM_DIR)
    _wait_fired(fired, seconds=4)
    assert "args" not in fired
    # run_id=None 时快照不落库，断言只能看线程日志（打的是「不触发」不是快照文案）
    assert "不触发 STRM" in capsys.readouterr().out


def test_scrape_dest_dirs_only_from_claimed(monkeypatch):
    """清单五销项：同名裸名旧记录（别的剧、id 甚至更新）不把它的整理目录混进 dests——
    按原始路径认领后，dests 只能来自转存落点下的记录。"""
    monkeypatch.setattr(
        rw.qms, "scrape_pathes",
        lambda: [{"id": 8, "source_path": WATCH_ROOT, "dest_path": "/kuake/1.影视/电影"}],
    )
    # 旧记录 id=20 大于分界线 5，但原始路径在别的剧目录下 → 只靠 id 分界挡不住，路径才挡得住
    same_name_elsewhere = [_rec(20, "17.mp4", "renamed",
                                src_dir="/kuake/待整理-电视剧/狂飙", new_path="华语剧集/狂飙")]
    mine = [_rec(25, "17.mp4", "renamed")]
    monkeypatch.setattr(
        rw.qms, "scrape_records",
        lambda page_size=1000, page=1, name=None, status=None: same_name_elsewhere + mine,
    )

    dests = rw._scrape_dest_dirs({"17.mp4"}, CLAIM_DIR, 5)
    assert dests == ["/kuake/1.影视/电影/华语电影/这个杀手不太冷静 (2022)"]


def test_records_window_reads_beyond_first_page(monkeypatch):
    """清单一回归：并发大批量把本场的记录挤出第一页后，窗口读取仍能认领到——
    旧实现只拉最新一页，此处会认领不到、STRM 静默回退全库同步。"""
    flood = [
        {"id": 10000 - i, "file_name": f"f{i:04d}.mkv", "status": "renamed",
         "path": "/kuake/待整理-电视剧/别的剧", "source_full_path": f"/kuake/待整理-电视剧/别的剧/f{i:04d}.mkv",
         "new_path": "华语剧集/别的剧"}
        for i in range(1000)
    ]
    mine = [_rec(100, "a.mkv", "renamed"), _rec(101, "b.mkv", "renamed")]

    def fake_records(page_size=1000, page=1, name=None, status=None):
        if page == 1:
            return flood  # 第一页全是并发批量产生的新记录（挤占窗口）
        if page == 2:
            return mine  # 本场记录被挤到第二页
        return []

    monkeypatch.setattr(rw.qms, "scrape_records", fake_records)
    claimed = rw.claim_records(CLAIM_DIR, {"a.mkv", "b.mkv"}, 50)
    assert set(claimed) == {"a.mkv", "b.mkv"}


# ---------- STRM 同步记录终态验证（2026-10-13 清单十一）----------

SYNC_DEST = "/kuake/1.影视/电影/华语电影/这个杀手不太冷静 (2022)"


def _mock_fire_env(monkeypatch, records, refresh_calls):
    """_fire_strm 的公共假件：定向任务受理成功，同步记录/Emby 按用例注入。"""
    monkeypatch.setattr(
        rw.qms, "get_sync_path",
        lambda sid: {"source_type": "openlist", "remote_path": "kuake/1.影视/电影",
                     "local_path": "/media/QMS", "account_id": 1},
    )
    monkeypatch.setattr(rw, "_scrape_dest_dirs", lambda names, claim_dir="", since_id=0: [SYNC_DEST])
    monkeypatch.setattr(
        rw.qms, "manual_sync",
        lambda path_id, path, target, account_id, is_file=False: (True, ""),
    )
    monkeypatch.setattr(rw.qms, "sync_records", lambda page_size=200: records)
    def fake_refresh(path_hint=None):
        refresh_calls["n"] += 1
        return True, "已按库刷新「华语电影」"
    monkeypatch.setattr(rw.qms, "refresh_emby_library", fake_refresh)
    monkeypatch.setattr(rw.qms, "emby_library_name", lambda hint: "华语电影")
    monkeypatch.setattr(rw.time, "sleep", lambda s: None)


def test_fire_strm_verifies_via_sync_records(monkeypatch):
    """同步记录到终态（status=2）：快照如实升级「已生成（新增 N 个 STRM）」，
    Emby 同步完成即刷（不再 90s 盲等）。"""
    rec = {"id": 99, "status": 2, "new_strm": 1, "remote_path": SYNC_DEST,
           "local_path": "/media/QMS", "created_at": int(time.time()), "fail_reason": ""}
    refresh_calls = {"n": 0}
    _mock_fire_env(monkeypatch, [rec], refresh_calls)
    snap = rw._fire_strm(8, {"这个杀手不太冷静 (2022).mkv"}, CLAIM_DIR, 0)
    assert snap == {"st": "已生成（新增 1 个 STRM）", "cls": "t-ok", "emby": "华语电影"}
    assert refresh_calls["n"] == 1


def test_fire_strm_reports_sync_failure(monkeypatch):
    """同步记录 status=3：快照标红带 fail_reason（确定性结果，用户自己去 QMS 调整），
    没生成东西就不刷 Emby。"""
    rec = {"id": 99, "status": 3, "new_strm": 0, "remote_path": SYNC_DEST,
           "local_path": "/media/QMS", "created_at": int(time.time()),
           "fail_reason": "路径 115盘/影视/电视剧 不存在：object not found"}
    refresh_calls = {"n": 0}
    _mock_fire_env(monkeypatch, [rec], refresh_calls)
    snap = rw._fire_strm(8, {"这个杀手不太冷静 (2022).mkv"}, CLAIM_DIR, 0)
    assert snap["cls"] == "t-bad"
    assert "同步失败" in snap["st"] and "object not found" in snap["st"]
    assert refresh_calls["n"] == 0


def test_fire_strm_falls_back_when_records_missing(monkeypatch):
    """等不到同步记录（超时/QMS 异常）：回落旧口径「已触发」+ Emby 90s 定时补刷。"""
    refresh_calls = {"n": 0}
    _mock_fire_env(monkeypatch, [], refresh_calls)
    monkeypatch.setattr(rw, "SYNC_WAIT_TIMEOUT", 0.2)
    snap = rw._fire_strm(8, {"这个杀手不太冷静 (2022).mkv"}, CLAIM_DIR, 0)
    assert snap == {"st": "已触发", "cls": "t-ok", "emby": "华语电影"}
