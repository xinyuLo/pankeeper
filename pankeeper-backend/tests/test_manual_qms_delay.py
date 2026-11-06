"""搜索转存（manual 链路）的「QMS 触发时间」等待——2026-11-07 回归守卫。

背景：226c455 上「QMS/STRM 反转」时，manual.py 里**非反转路径**的 _sleep_phase 整段
没了（auto.py 的 else 分支留着），队列配置的「QMS 触发时间」在搜索转存这条路上失效。
现场实录（记录 95「小城良方 (2026)」）：转存完成下一行就是「QMS 刮削任务 #10 触发成功」，
中间没有任何等待——百度转存是异步的，秒触发极易扫到空目录。

本文件锁两件事：顺序（先等 → 再触发）与秒数（cfg["qms"]，缺键回落 10，与 auto 同款）。
全部 mock（适配器/QMS/后台链路），零真实网盘请求；库用 conftest 的临时库。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.adapters.base import ShareFile, TransferResult
from app.services import media_push, notify, run_watch
from app.transfer import manual as manual_mod

MARK = "https://pan.quark.cn/s/manual-qms-delay"


def _eng():
    lock = threading.Lock()
    return SimpleNamespace(_lock=lock, _cond=threading.Condition(lock), state={})


def _t(**extra) -> dict:
    t = {
        "logs": [], "name": "测试剧.S01E01", "type": "quark", "shareUrl": MARK,
        "shareCode": "", "path": "/待整理", "includeSubdirs": True,
        # 显式给 QMS（弹窗下拉）：跳过目录配置匹配，判定只看等待与触发顺序
        "qmsId": 1, "strmId": None,
    }
    t.update(extra)
    return t


class _OkAdapter:
    """解析 + 转存各一个文件，让流程走到联动段（result.add=1）。"""

    def list_share(self, spec):
        return [ShareFile(fid="1", name="EP.mkv", path="/EP.mkv", size=1)]

    def save_files(self, files, spec, on_progress, on_log):
        on_progress(100)
        r = TransferResult()
        r.add = 1
        r.transferred = [{"name": "EP.mkv", "fid": "9"}]
        return r


@pytest.fixture(autouse=True)
def _clean_db():
    """台账（transfer_ledger）按 share_url+目录+文件名去重，不清会让第二个用例直接
    「无新增」短路、根本走不到联动段（假绿）。"""
    from app.db import SessionLocal
    from app.models import Record, TransferLedger

    def wipe():
        with SessionLocal() as s:
            s.query(TransferLedger).delete()
            s.query(Record).filter(Record.share_url == MARK).delete()
            s.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def events(monkeypatch):
    """把「等待」「触发」串成一条时间线，后台链路与推送全部接管。"""
    line: list[tuple] = []

    monkeypatch.setattr(manual_mod, "make_adapter", lambda *a, **k: _OkAdapter())
    monkeypatch.setattr(
        manual_mod, "_sleep_phase",
        lambda eng, t, phase, seconds, log_txt: line.append(("sleep", phase, int(seconds), log_txt)),
    )
    monkeypatch.setattr(manual_mod.qms, "trigger_scrape", lambda qid: (line.append(("trigger_scrape", int(qid))) or (True, "")))
    monkeypatch.setattr(manual_mod.qms, "trigger_strm", lambda sid: (line.append(("trigger_strm", int(sid))) or (True, "")))
    monkeypatch.setattr(run_watch, "record_fingerprint", lambda names: {})
    monkeypatch.setattr(run_watch, "verify_scrape_started", lambda *a, **k: None)
    monkeypatch.setattr(run_watch, "watch_qms", lambda *a, **k: None)
    monkeypatch.setattr(run_watch, "trigger_strm_after_scrape", lambda *a, **k: None)
    monkeypatch.setattr(run_watch, "touch_records", lambda: None)
    monkeypatch.setattr(run_watch, "write_qms_interim", lambda *a, **k: None)
    monkeypatch.setattr(media_push, "watch_and_spawn", lambda ctx: None)
    monkeypatch.setattr(notify, "push", lambda *a, **k: None)
    return line


@pytest.mark.parametrize("cfg, expect_seconds", [({"qms": 7, "strm": 3}, 7), ({}, 10)])
def test_manual_waits_queue_cfg_qms_before_trigger(events, cfg, expect_seconds):
    """非反转路径：先 _sleep_phase(waitqms, cfg["qms"]) 再 trigger_scrape（缺键回落 10）。"""
    manual_mod.run_manual(_eng(), _t(), cfg)

    assert events[0][:3] == ("sleep", "waitqms", expect_seconds), events
    assert events[1][:2] == ("trigger_scrape", 1), events
    # 等待日志文案带秒数，前端日志区要能看出来等了几秒
    assert f"{expect_seconds} 秒后触发 QMS 刮削" in events[0][3]


def test_manual_reverse_still_waits_both_phases(events, monkeypatch):
    """反转路径（有 STRM）原有两段等待不受影响：qms 秒 → STRM → strm 秒 → QMS。"""
    cfg = {"qms": 4, "strm": 6}
    # 反转开关是队列配置的全局键（走 get_group），这里直接把它掰开
    monkeypatch.setattr(manual_mod, "get_group", lambda key: {"reverse": True, **cfg})
    manual_mod.run_manual(_eng(), _t(strmId=2), cfg)

    assert [e[:3] for e in events[:4]] == [
        ("sleep", "waitqms", 4),
        ("trigger_strm", 2),
        ("sleep", "waitstrm", 6),
        ("trigger_scrape", 1),
    ], events
