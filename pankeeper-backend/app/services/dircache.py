from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from .settings_svc import get_group

class DirTreeCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: OrderedDict[tuple, tuple[dict, int, int]] = OrderedDict()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._restored = False

    @staticmethod
    def _db_upsert(key: tuple, data: Any, exp: int, cached_at: int, enabled: bool = True) -> None:
        if not enabled:
            return
        from ..models import DirTreeCacheRow
        from ..db import SessionLocal

        try:
            with SessionLocal() as s:
                row = s.get(DirTreeCacheRow, (key[0], key[1], key[2]))
                if row is None:
                    row = DirTreeCacheRow(type=key[0], acc=key[1], cid=key[2])
                    s.add(row)
                row.items_json = json.dumps(data, ensure_ascii=False)
                row.expires_at = int(exp)
                row.cached_at = int(cached_at)
                s.commit()
        except Exception:
            pass

    @staticmethod
    def _db_delete(key: tuple, enabled: bool = True) -> None:
        if not enabled:
            return
        from ..models import DirTreeCacheRow
        from ..db import SessionLocal

        try:
            with SessionLocal() as s:
                row = s.get(DirTreeCacheRow, (key[0], key[1], key[2]))
                if row is not None:
                    s.delete(row)
                    s.commit()
        except Exception:
            pass

    def _ensure_restored(self) -> None:
        if self._restored:
            return
        from ..models import DirTreeCacheRow
        from ..db import SessionLocal

        if not self._persist_enabled():
            self._restored = True
            return
        try:
            with SessionLocal() as s:
                rows = s.query(DirTreeCacheRow).all()
        except Exception as e:
            print(f"[cache] 目录缓存恢复失败，将在下次访问时重试：{e}")
            return
        self._restored = True
        now = time.time()
        for r in rows:
            if r.expires_at <= now:
                continue
            try:
                data = json.loads(r.items_json or "[]")
            except ValueError:
                continue
            key = (r.type, r.acc, r.cid)
            self._store[key] = (data, r.expires_at, r.cached_at)
            self._bytes += self._est_size(data)
        if self._store:
            print(f"[cache] 已从数据库恢复 {len(self._store)} 个目录缓存条目")
            self._evict_if_needed()

    @staticmethod
    def _est_size(data: Any) -> int:
        import json as _json

        try:
            return max(256, len(_json.dumps(data, ensure_ascii=False).encode()))
        except (TypeError, ValueError):
            return 1024

    def _cfg(self) -> dict:
        return get_group("cache_cfg")

    def _ttl_seconds(self) -> int:
        cfg = self._cfg()
        if not cfg.get("master", True):
            return 0
        ttl = int(cfg.get("ttl") or 0)
        return ttl * (60 if cfg.get("ttlUnit") == "分钟" else 3600)

    def _max_entries(self) -> int:
        return int(self._cfg().get("maxEntries") or 5000)

    def _persist_enabled(self) -> bool:
        return bool(self._cfg().get("persist", True))

    def get_or_load(self, key: tuple, loader: Callable[[], Any], force: bool = False, flag: dict | None = None) -> Any:
        ttl = self._ttl_seconds()
        if ttl <= 0 or force:
            if flag is not None:
                flag["cached"] = False
            data = loader()
            if ttl > 0:
                now = time.time()
                persist = self._persist_enabled()
                with self._lock:
                    self._store.pop(key, None)
                    self._store[key] = (data, now + ttl, now)
                    self._store.move_to_end(key)
                    self._bytes += self._est_size(data)
                    self._evict_if_needed(persist)
                self._db_upsert(key, data, now + ttl, now, persist)
            return data
        with self._lock:
            self._ensure_restored()
            hit = self._store.get(key)
            if hit and hit[1] > time.time():
                self._store.move_to_end(key)
                self.hits += 1
                if flag is not None:
                    flag["cached"] = True
                return hit[0]
            if hit:
                self._store.pop(key)
            klock = self._key_locks.setdefault(key, threading.Lock())
        with klock:

            with self._lock:
                hit = self._store.get(key)
                if hit and hit[1] > time.time():
                    self.hits += 1
                    if flag is not None:
                        flag["cached"] = True
                    return hit[0]
            if flag is not None:
                flag["cached"] = False
            data = loader()
            now = time.time()
            persist = self._persist_enabled()
            with self._lock:
                self.misses += 1
                self._store[key] = (data, now + ttl, now)
                self._bytes += self._est_size(data)
                self._evict_if_needed(persist)
            self._db_upsert(key, data, now + ttl, now, persist)
            return data

    def invalidate(self, key: tuple) -> None:
        with self._lock:
            self._store.pop(key, None)
        self._db_delete(key, self._persist_enabled())

    def update(self, key: tuple, fn) -> None:
        try:
            with self._lock:
                self._ensure_restored()
                hit = self._store.get(key)
                if not hit or hit[1] <= time.time():
                    return
                data, exp, cached_at = hit
                new_data = fn(data)
                if new_data is not None:
                    self._store[key] = (new_data, exp, cached_at)
                    self._store.move_to_end(key)
            self._db_upsert(key, new_data, exp, cached_at, self._persist_enabled())
        except Exception:
            pass

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            self._bytes = 0
        self._db_clear(self._persist_enabled())
        return n

    @staticmethod
    def _db_clear(enabled: bool = True) -> None:
        if not enabled:
            return
        from ..models import DirTreeCacheRow
        from ..db import SessionLocal

        try:
            with SessionLocal() as s:
                s.query(DirTreeCacheRow).delete()
                s.commit()
        except Exception:
            pass

    def _max_bytes(self) -> int:
        return max(1, int(self._cfg().get("maxSizeMb") or 100)) * 1024 * 1024

    def _evict_if_needed(self, persist: bool = True) -> None:
        max_n = max(16, self._max_entries())
        limit = self._max_bytes()
        while self._store and (self._bytes > limit or len(self._store) > max_n):
            key, (data, _, _) = self._store.popitem(last=False)
            self._bytes -= self._est_size(data)
            self.evictions += 1
            self._db_delete(key, persist)

    def stats(self) -> dict:
        with self._lock:
            self._ensure_restored()
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": round(self.hits / total * 100, 1) if total else 0.0,
            }

    def list_entries(self) -> list[dict]:
        with self._lock:
            self._ensure_restored()
            now = time.time()
            out = []
            for (t, acc, cid), (data, exp, cached_at) in self._store.items():
                ttl_min = int((exp - now) / 60)
                n = len(data) if isinstance(data, list) else 0
                out.append(
                    {
                        "type": t,
                        "acc": acc,
                        "path": str(cid),
                        "entries": n,
                        "size": f"{max(1, n * 2)} KB",
                        "ttlMin": ttl_min,
                        "cachedAt": int(cached_at),
                    }
                )
            return out

dir_cache = DirTreeCache()
