"""清单四回归：QMS 触发失败（转存成功）必须即时如实推送——不进 30 分钟记录轮询。

信息条统一「❌ QMS 触发失败 · ❌ STRM 未触发」，反转模式不做特判（2026-10-13 用户定稿），
也不出现「（QMS 连锁）」措辞。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.media_push as mp  # noqa: E402


def _ctx(**over):
    ctx = {"drive": "quark", "task": "测试影片", "names": ["a.mkv"], "source": "search"}
    ctx.update(over)
    return ctx


def test_stats_qms_failed_uniform():
    stats = mp._stats_list(_ctx(qms_ok=False), [], None)
    assert "❌ QMS 触发失败" in stats
    assert "❌ STRM 未触发" in stats
    assert not any("连锁" in s for s in stats)
    assert not any("反转" in s for s in stats)


def test_watch_pushes_immediately_without_polling(monkeypatch):
    pushed = []

    monkeypatch.setattr(mp.notify, "push", lambda *a, **k: pushed.append((a, k)))

    def _boom(ctx):
        raise AssertionError("QMS 触发失败不得进记录轮询（没得等）")

    monkeypatch.setattr(mp, "_wait_records", _boom)
    mp._watch(_ctx(qms_ok=False))
    assert len(pushed) == 1
    assert "QMS 触发失败" in pushed[0][0][0]
    assert "a.mkv" in pushed[0][0][1]
