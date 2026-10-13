"""清单七回归：转存配置嵌套目录时 _match_dd_link 取最长前缀（最具体的配置赢），
与添加顺序无关——旧实现按库序首个命中，宽目录先添加时会盖住窄目录的专用配置。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models import DdItem  # noqa: E402
from app.transfer.manual import _match_dd_link  # noqa: E402


def _add(path: str, qms_id: int, strm_id: int) -> None:
    with SessionLocal() as s:
        s.add(DdItem(type="quark", name=path, path=path, qms_on=True, qms_id=qms_id, strm_id=strm_id))
        s.commit()


def test_match_dd_link_longest_prefix_wins():
    _add("kuake/影视", 1, 8)  # 宽目录先添加
    _add("kuake/影视/电视剧", 2, 9)  # 窄目录后添加
    link = _match_dd_link("kuake/影视/电视剧/某剧")
    assert link == {"qms_id": 2, "strm_id": 9}


def test_match_dd_link_broad_dir_still_hits():
    link = _match_dd_link("kuake/影视/电影/某片")
    assert link == {"qms_id": 1, "strm_id": 8}
