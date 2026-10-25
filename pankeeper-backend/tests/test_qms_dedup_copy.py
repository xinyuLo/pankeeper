"""T-008：QMS 去重（dedup）不该报「未见记录」——快照终态 + 推送统计段口径。

场景复现（2026-10-25 17:02 实录）：转存 E17 后 QMS 日志「文件已在数据库中，跳过」，
没产生新记录；_wait 判 dedup 是对的，但推送把空 records 算成 missing 渲染成
「QMS 1 项未见记录」，运行详情也一直挂着「已触发」不升级。
"""
from __future__ import annotations

import json

import app.services.media_push as mp
import app.services.run_watch as rw
from app.db import SessionLocal
from app.models import RunHistory


def test_run_watch_dedup_writes_terminal_snapshot(monkeypatch):
    """验收 1：baseline 命中 + 记录池纹丝不动（无新记录）→ 快照写「已在库（未重刮）」
    终态（t-ok），文案不含「未见」。"""
    monkeypatch.setattr(rw.qms, "scrape_records", lambda **kw: [{"id": 5, "file_name": "EP17.mkv", "status": "renamed"}])
    monkeypatch.setattr(rw, "DEDUP_WAIT", 0)  # 不真等 120 秒
    monkeypatch.setattr(rw, "POLL_INTERVAL", 0.01)  # Windows time.time() ~15ms 量化：DEDUP_WAIT=0 时首迭代可能判等不过，别白等 30 秒

    with SessionLocal() as s:
        h = RunHistory(task_id=99999, started="x", status="running")
        s.add(h)
        s.commit()
        rid = h.id

    rw._watch(rid, "剧名", ["EP17.mkv"], {"EP17.mkv": [5, "renamed"]})

    with SessionLocal() as s:
        snap = json.loads(s.get(RunHistory, rid).qms_json or "{}")
    assert snap["st"] == "已在库（未重刮）"
    assert snap["cls"] == "t-ok"
    assert "未见" not in snap["st"]


def _dedup_snap(monkeypatch, status: str) -> dict:
    """dedup 场景跑一遍 _watch，返回写回的快照。baseline 记录与记录池同一条
    （同 id 同状态 → fresh 为空 → verdict=dedup）。"""
    monkeypatch.setattr(rw.qms, "scrape_records", lambda **kw: [{"id": 5, "file_name": "EP17.mkv", "status": status}])
    monkeypatch.setattr(rw, "DEDUP_WAIT", 0)
    monkeypatch.setattr(rw, "POLL_INTERVAL", 0.01)

    with SessionLocal() as s:
        h = RunHistory(task_id=99999, started="x", status="running")
        s.add(h)
        s.commit()
        rid = h.id

    rw._watch(rid, "剧名", ["EP17.mkv"], {"EP17.mkv": [5, status]})

    with SessionLocal() as s:
        return json.loads(s.get(RunHistory, rid).qms_json or "{}")


def test_dedup_old_scrape_failed_not_whitewashed(monkeypatch):
    """T-008-F2：baseline 挂 scrape_failed → dedup 写「刮削失败（未重刮）」t-bad，
    不许粉饰成绿色「已在库」。"""
    snap = _dedup_snap(monkeypatch, "scrape_failed")
    assert snap["cls"] == "t-bad"
    assert "刮削失败" in snap["st"] and "未重刮" in snap["st"]


def test_dedup_old_rename_failed_not_whitewashed(monkeypatch):
    """T-008-F2：rename_failed 同样 t-bad。"""
    snap = _dedup_snap(monkeypatch, "rename_failed")
    assert snap["cls"] == "t-bad"


def test_dedup_all_renamed_stays_ok(monkeypatch):
    """T-008-F2：全是 renamed 才写 t-ok「已在库」。"""
    snap = _dedup_snap(monkeypatch, "renamed")
    assert snap["cls"] == "t-ok"
    assert "已在库" in snap["st"]


def test_fallback_dedup_body_has_no_missing_annotation(monkeypatch):
    """T-008-F1：dedup 时 _fallback 正文不再逐文件挂「（未见 QMS 刮削记录）」；
    非 dedup 的空记录仍如实标未见。"""
    sent: dict = {}
    monkeypatch.setattr(mp.notify, "push", lambda title, body, **kw: sent.update(title=title, body=body))

    mp._fallback({"names": ["EP17.mkv"], "qms_ok": True, "qms_verdict": "dedup", "task": "剧名"}, [], None, None)
    assert "未见" not in sent["body"]  # 卡片里不再同屏出现「已在库」+「未见记录」

    mp._fallback({"names": ["EP17.mkv"], "qms_ok": True, "task": "剧名"}, [], None, None)
    assert "未见 QMS 刮削记录" in sent["body"]  # 真没刮到仍如实标


def test_stats_list_dedup_shows_in_library():
    """验收 2：dedup verdict 下推送统计段为「已在库（未重刮）」。"""
    ctx = {"names": ["EP17.mkv"], "qms_ok": True, "qms_verdict": "dedup"}
    stats = mp._stats_list(ctx, [], None)
    assert any("已在库" in s and "未重刮" in s for s in stats)
    assert not any("未见" in s for s in stats)


def test_stats_list_empty_records_non_dedup_still_reports_missing():
    """验收 3（防回退）：非 dedup 的空记录（stale/timeout 路径）仍如实报「未见记录」。"""
    ctx = {"names": ["EP17.mkv"], "qms_ok": True}  # 无 verdict
    stats = mp._stats_list(ctx, [], None)
    assert any("1 项未见记录" in s for s in stats)


def test_wait_records_propagates_verdict(monkeypatch):
    """verdict 一路带回（_watch 接线用），不再被丢弃。"""
    monkeypatch.setattr(rw, "_wait", lambda names, baseline=None, on_state=None: ([], "dedup"))
    recs, verdict = mp._wait_records({"names": ["EP17.mkv"], "qms_baseline": {}})
    assert recs == [] and verdict == "dedup"
