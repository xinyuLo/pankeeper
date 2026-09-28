"""缓存配置（目录树缓存策略 + 内存观测）与目录浏览（转存弹窗懒加载）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..deps import CurrentUser, make_adapter_for
from ..models import DirPathCache
from ..services.dircache import dir_cache
from ..services.settings_svc import get_group, save_group

router = APIRouter(prefix="/api", tags=["cache"])


@router.get("/cache/config")
def get_cache_config(_user=CurrentUser):
    cfg = get_group("cache_cfg")
    stats = dir_cache.stats()
    # 水位条语义：条目占用 / 条目上限（真实进程内存水位留在 M2 接 psutil）
    max_n = max(1, int(cfg.get("maxEntries") or 500))
    pct = min(100, round(stats["entries"] / max_n * 100))
    mem = {"pct": pct, "usedGb": stats["entries"], "totalGb": max_n}
    return {"cfg": cfg, "mem": mem}


@router.put("/cache/config")
def put_cache_config(body: dict, _user=CurrentUser):
    save_group("cache_cfg", body)
    return get_group("cache_cfg")


@router.get("/cache/trees")
def list_cache_trees(_user=CurrentUser):
    """裸数组返回（前端 CacheTree[]），id = "type/account/cid" 组合键。"""
    entries = dir_cache.list_entries()
    for e in entries:
        e["id"] = f"{e['type']}/{e['acc']}/{e['path']}"
    return entries


@router.post("/cache/trees/{key}/refresh")
def refresh_tree(key: str, _user=CurrentUser):
    """key 形如 "quark/main/0"（type/account/cid）。"""
    parts = key.split("/", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="key 格式应为 type/account/cid")
    dir_cache.invalidate((parts[0], parts[1], parts[2]))
    return {"ok": True}


@router.delete("/cache/trees/{key}")
def clear_tree(key: str, _user=CurrentUser):
    return refresh_tree(key, _user)


@router.delete("/cache/trees")
def clear_all_trees(_user=CurrentUser):
    return {"count": dir_cache.clear()}


@router.post("/cache/trees/refresh-all")
def refresh_all(_user=CurrentUser):
    return {"count": dir_cache.clear()}  # 全部失效 = 下次浏览直连重拉


@router.get("/files/list")
def list_files(type: str = "quark", parent: str = "0", path: str = "", force_refresh: bool = False, _user=CurrentUser):
    """转存弹窗目录浏览：按父目录拉一层，缓存 key=(type, account, cid)。"""
    from ..security import decrypt_credential  # noqa: F401

    with SessionLocal() as db:
        adapter = make_adapter_for(db, type)

    def load():
        if type == "quark":
            if parent == "0" and path:
                # 按路径浏览：逐级解析到 fid（懒加载契约：前端只传父层）
                fid = _resolve_path(adapter, path)
                items = adapter._list_dir(fid)
            else:
                items = adapter._list_dir(parent)
            out = [
                {
                    "fid": str(it.get("fid", "")),
                    "name": str(it.get("file_name", "")),
                    "is_dir": bool(it.get("dir")),
                    "size": int(it.get("size") or 0),
                }
                for it in items
            ]
            # 目录 ID→路径映射（稳定，无 TTL）
            _remember_paths(type, parent, path, out)
            return out
        raise HTTPException(status_code=400, detail=f"网盘 {type} 适配器尚未实现")

    return dir_cache.get_or_load((type, "main", parent), load, force=force_refresh)


def _resolve_path(adapter, path: str) -> str:
    """路径 → fid：逐级查 dir_path_cache，miss 再向网盘确认。"""
    parts = [p for p in path.strip("/").split("/") if p]
    fid = "0"
    walked = ""
    for part in parts:
        walked += "/" + part
        with SessionLocal() as db:
            row = (
                db.query(DirPathCache)
                .filter(DirPathCache.account_type == "quark", DirPathCache.dir_path == walked)
                .first()
            )
        if row:
            fid = row.dir_id
            continue
        children = adapter._list_dir(fid)
        hit = next((c for c in children if c.get("file_name") == part and c.get("dir")), None)
        if hit is None:
            raise HTTPException(status_code=404, detail=f"目录不存在：{walked}")
        fid = str(hit["fid"])
    return fid


def _remember_paths(drive_type: str, parent: str, parent_path: str, items: list[dict]) -> None:
    """浏览时顺手 reconcile 目录映射表。"""
    base = parent_path.rstrip("/")
    with SessionLocal() as db:
        for it in items:
            if not it["is_dir"]:
                continue
            full = (base + "/" + it["name"]) if base else "/" + it["name"]
            row = db.get(DirPathCache, (drive_type, it["fid"]))
            if row is None:
                db.add(
                    DirPathCache(
                        account_type=drive_type,
                        dir_id=it["fid"],
                        dir_path=full,
                        parent_id=parent,
                        last_seen_at=int(time.time()),
                    )
                )
            else:
                row.dir_path = full
                row.last_seen_at = int(time.time())
        db.commit()
