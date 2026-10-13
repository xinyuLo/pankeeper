"""清单六 B 案回归：Bark markdown 超 APNs 载荷预算自动降级——从尾部按行丢、保头部
统计行/标题，极端单行巨文本硬截断；Server酱 desp 不经过这里（全量定案不受影响）。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.notify as notify  # noqa: E402


def test_short_markdown_untouched():
    md = "统计行\n\n## 片名\n\n\u2060　**简介：短简介**"
    assert notify._fit_payload_markdown(md, "标题") == md


def test_long_markdown_fitted_by_dropping_tail_lines():
    head = "头部统计行\n\n## 长片名\n\n"
    md = head + "".join(f"- line {i:03d} " + "x" * 100 + "\n" for i in range(40))
    out = notify._fit_payload_markdown(md, "☁️ 夸克 · 长片名 ✅ 成功")
    assert out.endswith("…（超长已截断）")
    assert len(out.encode("utf-8")) <= notify.BARK_PAYLOAD_BUDGET
    assert out.startswith("头部统计行")  # 头部统计行/标题保住，丢的是尾部


def test_extreme_single_line_hard_cut():
    md = "简" * 5000  # 单行 15000 字节，丢行无法降级
    out = notify._fit_payload_markdown(md, "t")
    assert len(out.encode("utf-8")) <= notify.BARK_PAYLOAD_BUDGET
    assert out.endswith("…（超长已截断）")
    assert out.startswith("简")  # 不是整段丢弃，是截到预算内
