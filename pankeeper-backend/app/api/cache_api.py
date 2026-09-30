"""缓存配置（目录树缓存策略 + 内存观测）与目录浏览（转存弹窗懒加载）。"""
from __future__ import annotations

import threading
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
    # 水位条语义：已用缓存字节 / 设置的缓存大小上限（MB）
    total_mb = max(1, int(cfg.get("maxSizeMb") or 800))
    used_mb = round(stats.get("bytes", 0) / 1024 / 1024, 1)
    pct = min(100, round(used_mb / total_mb * 100))
    mem = {"pct": pct, "usedMb": used_mb, "totalMb": total_mb}
    return {"cfg": cfg, "mem": mem, "stats": stats}


@router.put("/cache/config")
def put_cache_config(body: dict, _user=CurrentUser):
    save_group("cache_cfg", body)
    return get_group("cache_cfg")


@router.get("/cache/trees")
def list_cache_trees(_user=CurrentUser):
    """裸数组返回（前端 CacheTree[]），id = "type/account/cid" 组合键。

    acc 键里存的是 "main"（默认账号）或账号 id 字符串——对外统一解析成
    账号显示名（别名/昵称），不然列表里一排 main/2 用户看不懂。"""
    from ..db import SessionLocal
    from ..models import Account

    entries = dir_cache.list_entries()
    with SessionLocal() as db:
        accs = {a.id: a for a in db.query(Account).all()}
    dmap = get_group("default_accounts") or {}
    for e in entries:
        e["id"] = f"{e['type']}/{e['acc']}/{e['path']}"
        acc = e["acc"]
        row = None
        if acc == "main":
            did = dmap.get(e["type"])
            row = accs.get(did) if did else next((a for a in accs.values() if a.type == e["type"]), None)
        elif str(acc).isdigit():
            row = accs.get(int(acc))
        e["acc_name"] = (row.alias or row.nickname or f"{e['type']}#{row.id}") if row else acc
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
def list_files(type: str = "quark", parent: str = "0", path: str = "", force_refresh: bool = False, acc_id: int | None = None, _user=CurrentUser):
    """转存弹窗目录浏览：按父目录拉一层，缓存 key=(type, account, parent)。

    acc_id 空 = 该类型默认账号（缓存键记作 "main"）；指定账号则键里带账号 id，
    同一网盘不同账号的目录缓存互不串。"""
    acc_key = str(acc_id) if acc_id else "main"
    # 路径解析模式（parent='0' 且带 path）：键里必须带路径，否则会命中真根的缓存
    key_id = ("p:" + path) if (path and parent in ("0", "")) else parent
    return dir_cache.get_or_load(
        (type, acc_key, key_id),
        lambda: _load_dir_payload(type, acc_id, parent, path),
        force=force_refresh,
    )


def _load_dir_payload(type: str, acc_id: int | None, parent: str, path: str):
    """拉取并映射一层目录（/files/list 与全树预热共用）。调用方负责包进 dir_cache.get_or_load。"""
    with SessionLocal() as db:
        adapter = make_adapter_for(db, type, acc_id)
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
    if type == "baidu":
        # 百度没有 quark 那种 fid 概念，直接用**完整路径**当目录标识：
        # 根层前端传 parent='0' + path='/'；展开子层时 parent 就是上一层的 path。
        directory = path if (path and parent in ("0", "")) else parent
        if not directory or directory == "0":
            directory = "/"
        items = adapter.list_dir(directory)
        return [
            {
                "fid": str(it.get("path") or ""),
                "name": str(it.get("server_filename", "")),
                "is_dir": bool(it.get("isdir")),
                "size": int(it.get("size") or 0),
            }
            for it in items
        ]
    raise HTTPException(status_code=400, detail=f"网盘 {type} 适配器尚未实现")


# ===== 全树预热：保存 Cookie 验证通过后一次性把目录树灌进缓存 =====
# 上限防失控：目录数/深度到顶就停，剩余的浏览时按需缓存
WARM_MAX_DIRS = 300
WARM_MAX_DEPTH = 5
_WARM_JOBS: dict[str, dict] = {}
_WARM_LOCK = threading.Lock()


def _warm_walk(type: str) -> None:
    from collections import deque

    job = _WARM_JOBS[type]
    job.update(status="running", done=0, total=1, message="")
    try:
        # 根节点：quark 用 fid，baidu 用路径（fid 即完整路径）
        root = "0" if type == "quark" else "/"
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        seen: set[str] = set()
        while queue:
            key_id, depth = queue.popleft()
            if key_id in seen:
                continue
            seen.add(key_id)
            if job["done"] >= WARM_MAX_DIRS or depth > WARM_MAX_DEPTH:
                job["message"] = f"已达上限（{WARM_MAX_DIRS} 个目录 / {WARM_MAX_DEPTH} 层），其余浏览时按需缓存"
                break
            payload = dir_cache.get_or_load((type, "main", key_id), lambda: _load_dir_payload(type, None, key_id, ""))
            job["done"] += 1
            job["total"] = max(job["total"], job["done"])
            for it in payload:
                if it["is_dir"] and it["fid"] and it["fid"] not in seen:
                    job["total"] += 1
                    queue.append((it["fid"], depth + 1))
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 —— 预热失败不影响业务，状态报给前端
        job["status"] = "error"
        job["message"] = str(e)


@router.post("/cache/trees/warm")
def warm_trees(body: dict, _user=CurrentUser):
    """后台启动某网盘的全树预热，立即返回。重复调用时若已在跑则直接回当前进度。"""
    type = body.get("type") or ""
    if type not in ("baidu", "quark"):
        raise HTTPException(status_code=400, detail=f"网盘 {type} 暂不支持目录预热")
    with _WARM_LOCK:
        job = _WARM_JOBS.get(type)
        if job and job["status"] == "running":
            return {"type": type, **job}
        _WARM_JOBS[type] = {"status": "queued", "done": 0, "total": 1, "message": ""}
    threading.Thread(target=_warm_walk, args=(type,), daemon=True).start()
    return {"type": type, **_WARM_JOBS[type]}


@router.get("/cache/trees/warm/status")
def warm_status(type: str, _user=CurrentUser):
    job = _WARM_JOBS.get(type) or {"status": "idle", "done": 0, "total": 0, "message": ""}
    return {"type": type, **job}


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
