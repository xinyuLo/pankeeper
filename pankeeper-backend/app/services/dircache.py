"""目录树缓存（内存）：LRU + TTL + 水位降级 + singleflight + 命中率统计。

策略对齐 LitePan 已验证的模型与前端「缓存配置」页：
- 粒度按目录（不是整树）；空结果也缓存（防空目录穿透）；
- 同 key 并发未命中只放一个 loader 去打网盘（singleflight）；
- TTL<=0 或总开关关闭 = 完全禁用直连；
- 水位/条目上限从 cache_cfg 读（前端缓存配置页可调）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from .settings_svc import get_group


class DirTreeCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: OrderedDict[tuple, tuple[dict, int]] = OrderedDict()  # key → (data, expires_at)
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._bytes = 0  # 缓存内容近似字节数（水位条依据）
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @staticmethod
    def _est_size(data: Any) -> int:
        import json as _json

        try:
            return max(256, len(_json.dumps(data, ensure_ascii=False).encode()))
        except (TypeError, ValueError):
            return 1024

    # ---------- 配置 ----------

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

    # ---------- 读写 ----------

    def get_or_load(self, key: tuple, loader: Callable[[], Any], force: bool = False) -> Any:
        """命中返回缓存；未命中单飞加载。force=True 跳过缓存直连（不回写）。"""
        ttl = self._ttl_seconds()
        if ttl <= 0 or force:
            return loader()
        with self._lock:
            hit = self._store.get(key)
            if hit and hit[1] > time.time():
                self._store.move_to_end(key)
                self.hits += 1
                return hit[0]
            if hit:
                self._store.pop(key)
            klock = self._key_locks.setdefault(key, threading.Lock())
        with klock:
            # double check：等锁期间可能已被别的线程加载
            with self._lock:
                hit = self._store.get(key)
                if hit and hit[1] > time.time():
                    self.hits += 1
                    return hit[0]
            data = loader()
            with self._lock:
                self.misses += 1
                self._store[key] = (data, time.time() + ttl)
                self._bytes += self._est_size(data)
                self._evict_if_needed()
            return data

    def invalidate(self, key: tuple) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            self._bytes = 0
            return n

    def _max_bytes(self) -> int:
        return max(1, int(self._cfg().get("maxSizeMb") or 800)) * 1024 * 1024

    def _evict_if_needed(self) -> None:
        """双上限 LRU 逐出：缓存大小（maxSizeMb）优先，条目数做硬顶。"""
        max_n = max(16, self._max_entries())
        limit = self._max_bytes()
        while self._store and (self._bytes > limit or len(self._store) > max_n):
            _, (data, _) = self._store.popitem(last=False)
            self._bytes -= self._est_size(data)
            self.evictions += 1

    # ---------- 观测（缓存配置页展示） ----------

    def stats(self) -> dict:
        with self._lock:
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
        """已缓存目录表（缓存配置页）：key=(type, account, cid)。"""
        with self._lock:
            now = time.time()
            out = []
            for (t, acc, cid), (data, exp) in self._store.items():
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
                    }
                )
            return out


dir_cache = DirTreeCache()
