"""单元测试：不依赖网络与真实账号，临时数据目录隔离。

运行：.venv/Scripts/python -m pytest tests/ -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# 必须在导入 app 之前把数据目录指到临时位置
_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.quark import extract_share  # noqa: E402
from app.adapters.rate_gate import CircuitOpen, RateGate, RateLimited  # noqa: E402
from app.services.pansou import TYPE_MAP, _map_merged  # noqa: E402


# ---------- 夸克分享链接解析 ----------

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


# ---------- pansou 结果映射 ----------

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
    # 不支持的类型（magnet/天翼）被丢弃
    assert len(TYPE_MAP) == 7


# ---------- 限速门 ----------

def test_rate_gate_interval_and_circuit():
    import time as _t

    gate = RateGate("t", min_interval=0.1, cooldown=0.3, max_consecutive_fail=3)
    gate.wait()
    start = _t.monotonic()
    gate.wait()  # 第二次应被间隔卡住
    assert _t.monotonic() - start >= 0.09
    for _ in range(3):
        gate.on_failure()
    try:
        gate.wait()
        assert False, "应触发熔断"
    except CircuitOpen:
        pass
    _t.sleep(0.35)
    gate.wait()  # 冷却结束恢复，且连败清零
    gate.on_rate_limited()
    try:
        gate.wait()
        assert False, "限频后应退避"
    except CircuitOpen:
        pass


def test_rate_limited_is_exception():
    assert issubclass(RateLimited, Exception)


# ---------- 队列引擎（快照恢复 / 入队位次 / 出队裁剪） ----------

def _fresh_engine():
    from app.db import init_db

    init_db()
    from app.queue.engine import QueueEngine

    # 单测不起 worker 线程：跨实例线程会互相争抢共享的临时库，且这里只验状态机不验推进
    return QueueEngine(start_workers=False)


def test_queue_enqueue_pos_and_snapshot():
    eng = _fresh_engine()
    p1 = eng.enqueue({"name": "A", "type": "quark", "path": "/test", "share_url": "https://pan.quark.cn/s/a"})
    p2 = eng.enqueue({"name": "B", "type": "quark", "path": "/test", "share_url": "https://pan.quark.cn/s/b"})
    assert (p1, p2) == (1, 2)
    snap = eng.state_public()
    assert [t["name"] for t in snap["tasks"]] == ["A", "B"]
    assert snap["tasks"][0]["shareUrl"].endswith("/s/a")
    # worker 线程会在 gap 间隔后提任务上场，这里只验形状不验推进
    assert all(t["status"] in ("wait", "run") for t in snap["tasks"])


def test_queue_prune_done_after_30min():
    eng = _fresh_engine()
    eng.enqueue({"name": "C", "type": "quark", "path": "/t", "share_url": "https://pan.quark.cn/s/c"})
    with eng._lock:
        t = eng.state["tasks"][-1]
        t["status"] = "done"
        t["doneAt"] = int(time.time() * 1000) - 31 * 60 * 1000  # 31 分钟前完成
    eng.state_public()  # 内部会 prune
    assert all(x["name"] != "C" for x in eng.state["tasks"])


def test_settings_default_merge():
    from app.services.settings_svc import get_group, save_group

    cfg = get_group("queue_cfg")
    assert cfg["threads"] == 1 and cfg["qms"] == 10
    cfg["threads"] = 3
    save_group("queue_cfg", cfg)
    assert get_group("queue_cfg")["threads"] == 3
    assert get_group("queue_cfg")["gap"] == 5  # 其余默认值保留


def test_settings_masked_secret_kept():
    from app.services.settings_svc import get_group, save_group

    cur = get_group("settings")
    cur["notify"]["sendkey"] = "SCT123456"
    save_group("settings", cur)
    # 前端回传掩码（**** 开头）→ 保留库里的真实值
    cur2 = get_group("settings")
    cur2["notify"]["sendkey"] = "****3456"
    save_group("settings", cur2)
    assert get_group("settings")["notify"]["sendkey"] == "SCT123456"


def test_credential_roundtrip():
    from app.security import decrypt_credential, encrypt_credential

    cipher = encrypt_credential("quark_cookie=abc; __uid=1")
    assert cipher != "quark_cookie=abc; __uid=1"
    assert decrypt_credential(cipher) == "quark_cookie=abc; __uid=1"
