from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.quark import extract_share
from app.adapters.rate_gate import CircuitOpen, RateGate, RateLimited
from app.services.pansou import TYPE_MAP, _map_merged

def test_extract_share_basic():
    out = extract_share("https://pan.quark.cn/s/1a2b3c4d5e")
    assert out == {"pwd_id": "1a2b3c4d5e", "passcode": "", "pdir_fid": "0"}

def test_extract_share_with_pwd_and_dir():
    url = "https://pan.quark.cn/s/1a2b3c4d5e?pwd=ab12#/list/share/0123456789abcdef0123456789abcdef-第%201%20季"
    out = extract_share(url)
    assert out["pwd_id"] == "1a2b3c4d5e"
    assert out["passcode"] == "ab12"
    assert out["pdir_fid"] == "0123456789abcdef0123456789abcdef"

def test_extract_share_invalid():
    import pytest

    from app.adapters.base import AdapterError

    with pytest.raises(AdapterError):
        extract_share("https://pan.baidu.com/s/xxxx")

def test_map_merged_types_and_drop():
    merged = {
        "quark": [{"url": "https://pan.quark.cn/s/a", "password": "12ab", "note": "庆余年 4K", "datetime": "2026-09-01T10:00:00Z", "source": "tg:x"}],
        "aliyun": [{"url": "https://www.alipan.com/s/b", "password": "", "note": "沙丘", "datetime": "2026-09-02", "source": "plugin:y"}],
        "magnet": [{"url": "magnet:?xt=1", "note": "种子", "datetime": "", "source": ""}],
        "tianyi": [{"url": "https://189.cn/s/c", "note": "天翼", "datetime": "", "source": ""}],
    }
    rows = _map_merged(merged)
    types = {r["t"] for r in rows}
    assert types == {"quark", "ali"}
    quark_row = next(r for r in rows if r["t"] == "quark")
    assert quark_row["n"] == "庆余年 4K"
    assert quark_row["share_code"] == "12ab"
    assert quark_row["d"] == "2026-09-01"
    assert quark_row["url"].endswith("/s/a")

    assert len(TYPE_MAP) == 7

def test_rate_gate_interval_and_circuit():
    import time as _t

    gate = RateGate("t", min_interval=0.1, cooldown=0.3, max_consecutive_fail=3)
    gate.wait()
    start = _t.monotonic()
    gate.wait()
    assert _t.monotonic() - start >= 0.09
    for _ in range(3):
        gate.on_failure()
    try:
        gate.wait()
        assert False, "应触发熔断"
    except CircuitOpen:
        pass
    _t.sleep(0.35)
    gate.wait()
    gate.on_rate_limited()
    try:
        gate.wait()
        assert False, "限频后应退避"
    except CircuitOpen:
        pass

def test_rate_limited_is_exception():
    assert issubclass(RateLimited, Exception)

def _fresh_engine():
    from app.db import init_db

    init_db()
    from app.queue.engine import QueueEngine

    return QueueEngine(start_workers=False)

def test_queue_enqueue_pos_and_snapshot():
    eng = _fresh_engine()
    p1 = eng.enqueue({"name": "A", "type": "quark", "path": "/test", "share_url": "https://pan.quark.cn/s/a"})
    p2 = eng.enqueue({"name": "B", "type": "quark", "path": "/test", "share_url": "https://pan.quark.cn/s/b"})
    assert (p1, p2) == (1, 2)
    snap = eng.state_public()
    assert [t["name"] for t in snap["tasks"]] == ["A", "B"]
    assert snap["tasks"][0]["shareUrl"].endswith("/s/a")

    assert all(t["status"] in ("wait", "run") for t in snap["tasks"])

def test_queue_prune_done_after_1h():
    eng = _fresh_engine()
    eng.enqueue({"name": "C", "type": "quark", "path": "/t", "share_url": "https://pan.quark.cn/s/c"})
    with eng._lock:
        t = eng.state["tasks"][-1]
        t["status"] = "done"
        t["doneAt"] = int(time.time() * 1000) - 61 * 60 * 1000
    eng.state_public()
    assert all(x["name"] != "C" for x in eng.state["tasks"])

def test_settings_default_merge():
    from app.services.settings_svc import get_group, save_group

    cfg = get_group("queue_cfg")
    assert cfg["threads"] == 1 and cfg["qms"] == 10
    cfg["threads"] = 3
    save_group("queue_cfg", cfg)
    assert get_group("queue_cfg")["threads"] == 3
    assert get_group("queue_cfg")["gap"] == 5

def test_settings_masked_secret_kept():
    from app.services.settings_svc import get_group, save_group

    cur = get_group("settings")
    cur["notify"]["sendkey"] = "SCT123456"
    save_group("settings", cur)

    cur2 = get_group("settings")
    cur2["notify"]["sendkey"] = "****3456"
    save_group("settings", cur2)
    assert get_group("settings")["notify"]["sendkey"] == "SCT123456"

def test_credential_roundtrip():
    from app.security import decrypt_credential, encrypt_credential

    cipher = encrypt_credential("quark_cookie=abc; __uid=1")
    assert cipher != "quark_cookie=abc; __uid=1"
    assert decrypt_credential(cipher) == "quark_cookie=abc; __uid=1"

def _mk_file(name: str, md5: str = "", is_dir: bool = False):
    from app.adapters.base import ShareFile

    return ShareFile(fid="1", name=name, is_dir=is_dir, path=name, md5=md5)

def test_apply_regex_hit_and_miss():
    from app.transfer.auto import _apply_regex

    files = [_mk_file("第01集.mkv"), _mk_file("第02集.mkv"), _mk_file("4k.mp4"), _mk_file("海报.jpg"), _mk_file("子目录", is_dir=True)]
    kept, miss, hit = _apply_regex(files, r"\.mkv$")

    assert [f.name for f in kept if not f.is_dir] == ["第01集.mkv", "第02集.mkv"]
    assert any(f.is_dir for f in kept)
    assert miss == 2
    assert hit == ["第01集.mkv", "第02集.mkv"]

def test_apply_regex_invalid_raises():
    import re

    import pytest

    from app.transfer.auto import _apply_regex

    with pytest.raises(re.error):
        _apply_regex([_mk_file("a.mkv")], "([")

def test_apply_exclusion_by_name_and_md5():
    from app.transfer.auto import _apply_exclusion

    files = [
        _mk_file("广告.mp4"),
        _mk_file("改名了的预告.mp4", md5="aaa"),
        _mk_file("正片01.mkv", md5="bbb"),
        _mk_file("要排除的目录", is_dir=True),
    ]
    kept, excluded = _apply_exclusion(files, {"广告.mp4", "要排除的目录"}, {"aaa"})

    assert excluded == ["广告.mp4", "改名了的预告.mp4"]
    assert [f.name for f in kept] == ["正片01.mkv"]

def test_apply_exclusion_empty_lists_noop():
    from app.transfer.auto import _apply_exclusion

    files = [_mk_file("a.mkv"), _mk_file("b", is_dir=True)]
    kept, excluded = _apply_exclusion(files, set(), set())
    assert len(kept) == 2
    assert excluded == []

def test_queue_regex_pattern_reaches_state():
    eng = _fresh_engine()
    eng.enqueue({
        "name": "R", "type": "baidu", "path": "/t",
        "share_url": "https://pan.baidu.com/s/1abcdefghijklmnopqrstu",
        "regex_pattern": r"^40\..*4k.*\.mp4$",
    })
    snap = eng.state_public()
    mine = next(x for x in snap["tasks"] if x["name"] == "R")
    assert mine["regexPattern"] == r"^40\..*4k.*\.mp4$"

def test_queue_regex_pattern_survives_restore():
    from app.queue.engine import QueueEngine

    eng = _fresh_engine()
    eng.enqueue({
        "name": "R2", "type": "baidu", "path": "/t",
        "share_url": "https://pan.baidu.com/s/1zyxwvutsrqponmlkjihgfe",
        "regex_pattern": "4k",
    })
    eng2 = QueueEngine(start_workers=False)
    row = next(x for x in eng2.state["tasks"] if x["name"] == "R2")
    assert row["regexPattern"] == "4k"
