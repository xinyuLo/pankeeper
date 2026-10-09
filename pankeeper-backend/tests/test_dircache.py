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

from app.db import init_db

init_db()

from app.services.dircache import DirTreeCache
from app.services.settings_svc import get_group, save_group

def _small_cache(max_entries: int = 100) -> DirTreeCache:
    save_group("cache_cfg", {**get_group("cache_cfg"), "maxSizeMb": 800, "maxEntries": max_entries})
    DirTreeCache._db_clear(True)
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
    assert calls["n"] == 1
    st = c.stats()
    assert st["entries"] == 1 and st["hits"] == 1 and st["misses"] == 1

def test_invalidate_forces_reload():
    c = _small_cache()
    k = ("quark", "main", "0")
    c.get_or_load(k, lambda: ["v1"])
    c.invalidate(k)
    assert c.get_or_load(k, lambda: ["v2"]) == ["v2"]

def test_bytes_watermark_eviction():
    save_group("cache_cfg", {**get_group("cache_cfg"), "maxSizeMb": 1, "maxEntries": 100})
    c = DirTreeCache()
    big = [{"blob": "x" * (1024 * 1024)}]
    c.get_or_load(("q", "m", "1"), lambda: big)
    c.get_or_load(("q", "m", "2"), lambda: big)
    c.get_or_load(("q", "m", "3"), lambda: big)
    st = c.stats()
    assert st["evictions"] > 0
    assert st["entries"] < 3

def test_clear_resets_bytes():
    c = _small_cache()
    c.get_or_load(("q", "m", "1"), lambda: [{"a": 1}])
    assert c.clear() == 1
    assert c.stats()["entries"] == 0

def test_singleflight_concurrent_load_once():
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
    assert calls["n"] == 1
    assert len(results) == 4

def test_persistence_roundtrip():
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
    assert calls["n"] == 0
    c2._db_clear(True)

def test_persist_off_no_restore():
    save_group("cache_cfg", {**get_group("cache_cfg"), "persist": False})
    c1 = DirTreeCache()
    k = ("quark", "main", "persist-off")
    c1.get_or_load(k, lambda: [{"name": "y"}])
    c2 = DirTreeCache()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return []

    assert c2.get_or_load(k, loader) == []
    assert calls["n"] == 1
    save_group("cache_cfg", {**get_group("cache_cfg"), "persist": True})
    DirTreeCache._db_clear(True)
