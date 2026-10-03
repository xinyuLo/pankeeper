"""目录树缓存（内存 + SQLite 持久化）：LRU + TTL + 水位降级 + singleflight + 命中率统计。

策略对齐 LitePan 已验证的模型与前端「缓存配置」页：
- 粒度按目录（不是整树）；空结果也缓存（防空目录穿透）；
- 同 key 并发未命中只放一个 loader 去打网盘（singleflight）；
- TTL<=0 或总开关关闭 = 完全禁用直连；
- 水位/条目上限从 cache_cfg 读（前端缓存配置页可调）；
- **写穿持久化**（M3.5）：条目变化即时落 dir_tree_cache 表，重启懒恢复——
  测试/重启不再丢缓存，也不因恢复而对网盘补发请求。
"""
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
        self._store: OrderedDict[tuple, tuple[dict, int, int]] = OrderedDict()  # key → (data, expires_at, cached_at)
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._bytes = 0  # 缓存内容近似字节数（水位条依据）
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._restored = False  # 懒恢复标记：首次使用时从 SQLite 拉一次

    # ---------- 持久化（写穿 + 懒恢复） ----------

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
        except Exception:  # noqa: BLE001 —— 持久化失败不影响内存缓存工作
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
        except Exception:  # noqa: BLE001
            pass

    def _ensure_restored(self) -> None:
        """首次使用时从 SQLite 恢复未过期条目（幂等，持锁调用）。
        恢复失败（DB 忙/暂时不可用）**不置标记**：下次调用再试——否则整个进程
        生命周期都跑在无缓存模式，每次浏览真打网盘（-7 风控就是这么撞出来的）。"""
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
        except Exception as e:  # noqa: BLE001 —— 表还没建/库暂时忙：下次使用时重试
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

    def _persist_enabled(self) -> bool:
        return bool(self._cfg().get("persist", True))

    # ---------- 读写 ----------

    def get_or_load(self, key: tuple, loader: Callable[[], Any], force: bool = False, flag: dict | None = None) -> Any:
        """命中返回缓存；未命中单飞加载。force=True 跳过缓存直连并**把新数据回写缓存**。
        flag 传 dict 时回写命中标记（cached=True/False），供接口层区分直连与缓存。

        回写是对齐 share_cache 的 refresh 语义（2026-10-03）：曾只直连不回写，
        刷新给人看新的、缓存里旧的还在，重开弹窗又回到旧数据——刷新白点。"""
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
            # double check：等锁期间可能已被别的线程加载
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
        """就地更新缓存项：fn(data) -> data，保留剩余 TTL 并 LRU 置顶。

        写后局部更新用——转存/建删文件后把变化直接写进已缓存的目录列表，
        避免一次小改动就整层回源。key 不存在/已过期/更新抛错一律静默跳过
        （回源是懒加载兜底，这里只做锦上添花）。"""
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
        except Exception:  # noqa: BLE001 —— 同 bump：缓存辅助逻辑不能影响业务
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
        except Exception:  # noqa: BLE001
            pass

    def _max_bytes(self) -> int:
        return max(1, int(self._cfg().get("maxSizeMb") or 800)) * 1024 * 1024

    def _evict_if_needed(self, persist: bool = True) -> None:
        """双上限 LRU 逐出：缓存大小（maxSizeMb）优先，条目数做硬顶。"""
        max_n = max(16, self._max_entries())
        limit = self._max_bytes()
        while self._store and (self._bytes > limit or len(self._store) > max_n):
            key, (data, _, _) = self._store.popitem(last=False)
            self._bytes -= self._est_size(data)
            self.evictions += 1
            self._db_delete(key, persist)

    # ---------- 观测（缓存配置页展示） ----------

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
        """已缓存目录表（缓存配置页）：key=(type, account, cid)。"""
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
