"""分享文件清单缓存——与目录缓存（dircache）**刻意不同逻辑**：

- 键：分享链接（+提取码），不是目录路径；
- 刷新语义：**转存驱动**——自动/手动转存每跑完一次就刷新一次（里面可能新增了文件），
  查看/排除清单弹窗在两次转存之间命中缓存秒开；不按 TTL 过期；
- 持久化：写穿 SQLite（share_list_cache 表），重启不丢，恢复时过期条目直接丢弃
  （分享内容会变，重启后宁可重拉也不用旧的）。
"""
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

    # ---------- 持久化 ----------

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
        except Exception:  # noqa: BLE001 —— 持久化失败不影响主流程
            pass

    def _ensure_restored(self) -> None:
        """首次使用时从 SQLite 恢复（幂等，持锁调用）。恢复失败不置标记，下次再试。"""
        if self._restored:
            return
        from ..models import ShareListCacheRow
        from ..db import SessionLocal

        try:
            with SessionLocal() as s:
                rows = s.query(ShareListCacheRow).all()
        except Exception as e:  # noqa: BLE001 —— 表还没建/库暂时忙：下次使用时重试
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

    # ---------- 读写 ----------

    def get(self, key: str) -> tuple[Any, float] | None:
        """命中返回 (数据, cached_at)；未命中 None。"""
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
        """refresh=True 强制重拉；否则命中缓存秒回。返回 (数据, cached_at, 是否真拉了)。"""
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
        except Exception:  # noqa: BLE001
            pass


share_list_cache = ShareListCache()


def share_key(share_type: str, url: str, code: str) -> str:
    """分享清单缓存键：类型 + 链接 + 提取码。"""
    return f"{share_type}:{url}|{code}"


def build_payload(files: list) -> dict:
    """ShareFile 列表 → {total, tree, files}（tree 给查看弹窗，files 扁平给排除清单，带 md5）。

    树构建注意：无斜杠 = 分享顶层条目，parent 用哨兵 "/"——直接 rsplit 会把顶层
    节点的 parent 算成自己，整棵树被吞成空（实测踩坑）。
    """
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
