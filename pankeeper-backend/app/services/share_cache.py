from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from ..db import SessionLocal

class ShareListCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._restored = False

    def _db_upsert(self, key: str, data: Any, now: float) -> None:
        try:
            from ..models import ShareListCacheRow
            from ..db import SessionLocal

            with SessionLocal() as s:
                row = s.get(ShareListCacheRow, key)
                if row is None:
                    s.add(ShareListCacheRow(key=key, items_json=json.dumps(data, ensure_ascii=False), cached_at=now))
                else:
                    row.items_json = json.dumps(data, ensure_ascii=False)
                    row.cached_at = now
                s.commit()
        except Exception:
            pass

    def _ensure_restored(self) -> None:
        if self._restored:
            return
        from ..models import ShareListCacheRow
        from ..db import SessionLocal

        try:
            with SessionLocal() as s:
                rows = s.query(ShareListCacheRow).all()
        except Exception as e:
            print(f"[share-cache] 分享清单缓存恢复失败，将在下次访问时重试：{e}")
            return
        self._restored = True
        for r in rows:
            try:
                self._store[r.key] = (json.loads(r.items_json or "[]"), r.cached_at)
            except ValueError:
                continue
        if self._store:
            print(f"[share-cache] 已从数据库恢复 {len(self._store)} 个分享清单")

    def get(self, key: str) -> tuple[Any, float] | None:
        with self._lock:
            self._ensure_restored()
            hit = self._store.get(key)
            return (hit[0], hit[1]) if hit else None

    def put(self, key: str, data: Any) -> None:
        with self._lock:
            self._ensure_restored()
            now = time.time()
            self._store[key] = (data, now)
        self._db_upsert(key, data, now)

    def get_or_load(self, key: str, loader: Callable[[], Any], refresh: bool = False) -> tuple[Any, float, bool]:
        if not refresh:
            hit = self.get(key)
            if hit is not None:
                return (hit[0], hit[1], False)
        data = loader()
        self.put(key, data)
        return (data, time.time(), True)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
        try:
            from ..models import ShareListCacheRow
            from ..db import SessionLocal

            with SessionLocal() as s:
                row = s.get(ShareListCacheRow, key)
                if row is not None:
                    s.delete(row)
                    s.commit()
        except Exception:
            pass

share_list_cache = ShareListCache()

def share_key(share_type: str, url: str, code: str) -> str:
    return f"{share_type}:{url}|{code}"

def build_payload(files: list) -> dict:
    nodes: dict[str, dict] = {}
    root: list[dict] = []
    total = 0
    flat: list[dict] = []
    for f in sorted(files, key=lambda x: (x.path.count("/"), x.path)):
        parent = f.path.rsplit("/", 1)[0] if "/" in f.path else "/"
        name = f.path.rsplit("/", 1)[-1]
        n = {"name": name, "is_dir": f.is_dir, "size": f.size, "path": f.path, "kids": []}
        if not f.is_dir:
            total += 1
        nodes[f.path] = n
        flat.append({"path": f.path, "name": name, "is_dir": f.is_dir, "size": f.size, "md5": f.md5})
        pnode = nodes.get(parent)
        (pnode["kids"] if pnode else root).append(n)

    def sort_kids(ns: list[dict]) -> None:
        ns.sort(key=lambda x: (not x["is_dir"], x["name"]))
        for n in ns:
            sort_kids(n["kids"])

    sort_kids(root)
    return {"total": total, "tree": root, "files": flat}
