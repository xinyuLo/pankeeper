"""目录树缓存单元测试：加载一次/单飞/字节计量/超限逐出/失效/统计。

运行：.venv/Scripts/python -m pytest tests/test_dircache.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-dircache-")
os.environ["PK_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402

init_db()

from app.services.dircache import DirTreeCache  # noqa: E402
from app.services.settings_svc import get_group, save_group  # noqa: E402


def _small_cache(max_entries: int = 100) -> DirTreeCache:
    save_group("cache_cfg", {**get_group("cache_cfg"), "maxSizeMb": 800, "maxEntries": max_entries})
    DirTreeCache._db_clear(True)  # 持久化开启后新实例会从库里恢复，先清库保证用例隔离
    return DirTreeCache()


def test_load_once_and_stats():
    c = _small_cache()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [{"name": "a"}, {"name": "b"}]

    k = ("quark", "main", "0")
    assert c.get_or_load(k, loader) == [{"name": "a"}, {"name": "b"}]
    assert c.get_or_load(k, loader) == [{"name": "a"}, {"name": "b"}]
    assert calls["n"] == 1  # 第二次命中缓存，loader 只跑一次
    st = c.stats()
    assert st["entries"] == 1 and st["hits"] == 1 and st["misses"] == 1


def test_invalidate_forces_reload():
    c = _small_cache()
    k = ("quark", "main", "0")
    c.get_or_load(k, lambda: ["v1"])
    c.invalidate(k)
    assert c.get_or_load(k, lambda: ["v2"]) == ["v2"]


def test_bytes_watermark_eviction():
    """构造超大条目顶破 maxSizeMb：水位逐出后 bytes 回落、条目被清。"""
    save_group("cache_cfg", {**get_group("cache_cfg"), "maxSizeMb": 1, "maxEntries": 100})
    c = DirTreeCache()
    big = [{"blob": "x" * (1024 * 1024)}]  # ~1MB
    c.get_or_load(("q", "m", "1"), lambda: big)
    c.get_or_load(("q", "m", "2"), lambda: big)
    c.get_or_load(("q", "m", "3"), lambda: big)
    st = c.stats()
    assert st["evictions"] > 0  # 超过 1MB 上限触发了逐出
    assert st["entries"] < 3


def test_clear_resets_bytes():
    c = _small_cache()
    c.get_or_load(("q", "m", "1"), lambda: [{"a": 1}])
    assert c.clear() == 1
    assert c.stats()["entries"] == 0


def test_singleflight_concurrent_load_once():
    """并发同 key：loader 只执行一次（缓存击穿防护）。"""
    c = _small_cache()
    calls = {"n": 0}
    gate = threading.Event()

    def slow_loader():
        gate.wait(timeout=2)
        calls["n"] += 1
        return ["x"]

    results: list[int] = []

    def worker():
        c.get_or_load(("k", "k", "k"), slow_loader)
        results.append(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in threads:
        t.join(timeout=3)
    assert calls["n"] == 1  # 4 个并发只有 1 个真正加载
    assert len(results) == 4

def test_persistence_roundtrip():
    """写穿持久化：新实例（模拟重启）从 SQLite 恢复，零回源。"""
    DirTreeCache._db_clear(True)
    c1 = DirTreeCache()
    k = ("quark", "main", "persist-1")
    c1.get_or_load(k, lambda: [{"name": "x"}])

    c2 = DirTreeCache()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return []

    assert c2.get_or_load(k, loader) == [{"name": "x"}]
    assert calls["n"] == 0  # 命中恢复数据，没打 loader
    c2._db_clear(True)


def test_persist_off_no_restore():
    """持久化开关关闭：不恢复、不落库。"""
    save_group("cache_cfg", {**get_group("cache_cfg"), "persist": False})
    c1 = DirTreeCache()
    k = ("quark", "main", "persist-off")
    c1.get_or_load(k, lambda: [{"name": "y"}])
    c2 = DirTreeCache()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return []

    assert c2.get_or_load(k, loader) == []  # 没恢复到，回源了
    assert calls["n"] == 1
    save_group("cache_cfg", {**get_group("cache_cfg"), "persist": True})
    DirTreeCache._db_clear(True)
