from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import APIRouter, HTTPException

from ..adapters.base import AdapterError, CredentialExpired, RiskControlError
from ..db import SessionLocal
from ..deps import CurrentUser, make_adapter_for
from ..models import Account, DirPathCache
from ..services.dircache import dir_cache
from ..services.settings_svc import get_group, save_group

router = APIRouter(prefix="/api", tags=["cache"])

@router.get("/cache/config")
def get_cache_config(_user=CurrentUser):
    cfg = get_group("cache_cfg")
    stats = dir_cache.stats()

    total_mb = max(1, int(cfg.get("maxSizeMb") or 100))
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

@router.post("/cache/trees/{key:path}/refresh")
def refresh_tree(key: str, _user=CurrentUser):
    parts = key.split("/", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="key 格式应为 type/account/cid")
    type_, acc, cid = parts
    acc_id = int(acc) if acc.isdigit() else None

    parent, path = ("0", cid) if cid.startswith("/") else (cid, "")
    try:
        dir_cache.get_or_load(
            (type_, acc, cid),
            lambda: _load_dir_payload(type_, acc_id, parent, path),
            force=True,
        )
    except RiskControlError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}

@router.delete("/cache/trees/{key:path}")
def clear_tree(key: str, _user=CurrentUser):
    parts = key.split("/", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="key 格式应为 type/account/cid")
    dir_cache.invalidate((parts[0], parts[1], parts[2]))
    return {"ok": True}

@router.delete("/cache/trees")
def clear_all_trees(_user=CurrentUser):
    return {"count": dir_cache.clear()}

@router.post("/cache/trees/refresh-all")
def refresh_all(_user=CurrentUser):
    return {"count": dir_cache.clear()}

@router.get("/files/list")
def list_files(type: str = "quark", parent: str = "0", path: str = "", force_refresh: bool = False, acc_id: int | None = None, _user=CurrentUser):
    acc_key = str(acc_id) if acc_id else "main"

    key_id = path if (path and parent in ("0", "")) else parent
    flag: dict = {}
    try:
        items = dir_cache.get_or_load(
            (type, acc_key, key_id),
            lambda: _load_dir_payload(type, acc_id, parent, path),
            force=force_refresh,
            flag=flag,
        )
    except RiskControlError as e:

        raise HTTPException(status_code=429, detail=str(e))
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"cached": bool(flag.get("cached")), "items": items}

def _load_dir_payload(type: str, acc_id: int | None, parent: str, path: str):
    with SessionLocal() as db:
        adapter = make_adapter_for(db, type, acc_id)
    if type == "115":

        cid = adapter.path_to_cid(path) if (path and parent in ("0", "")) else parent
        rows = adapter._list_own_dir(cid)
        out = [
            {
                "fid": str(r.get("fid") or r.get("cid") or ""),
                "name": str(r.get("n") or r.get("fn") or ""),
                "is_dir": not (r.get("sha") or r.get("sha1")),
                "size": int(r.get("s") or 0),
            }
            for r in rows
        ]
        _remember_paths("115", parent, path, out)
        return out
    if type == "quark":
        if parent == "0" and path:

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

        _remember_paths(type, parent, path, out)
        return out
    if type == "baidu":

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

WARM_MAX_DIRS = 300
WARM_MAX_DEPTH = 5

_WARM_JOBS: dict[tuple, dict] = {}
_WARM_QUEUE: deque = deque()
_WARM_LOCK = threading.Lock()
_WARM_WORKER: threading.Thread | None = None
WARM_TYPES = ("baidu", "quark", "115")

def _warm_walk(type: str, acc_id: int | None, job: dict) -> None:
    job["status"] = "running"
    root = "0" if type in ("quark", "115") else "/"
    queue: deque = deque([(root, 0)])
    seen: set[str] = set()
    acc_key = str(acc_id) if acc_id else "main"
    try:
        while queue:
            key_id, depth = queue.popleft()
            if key_id in seen:
                continue
            seen.add(key_id)
            if job["done"] >= WARM_MAX_DIRS or depth > WARM_MAX_DEPTH:
                job["message"] = f"已达上限（{WARM_MAX_DIRS} 个目录 / {WARM_MAX_DEPTH} 层），其余浏览时按需缓存"
                break
            payload = dir_cache.get_or_load(
                (type, acc_key, key_id),
                lambda: _load_dir_payload(type, acc_id, key_id, ""),
            )
            job["done"] += 1
            job["total"] = max(job["total"], job["done"])
            for it in payload:
                if it["is_dir"] and it["fid"] and it["fid"] not in seen:
                    job["total"] += 1
                    queue.append((it["fid"], depth + 1))
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["message"] = str(e)

def _warm_worker() -> None:
    global _WARM_WORKER
    while True:
        with _WARM_LOCK:
            if not _WARM_QUEUE:
                _WARM_WORKER = None
                return
            key = _WARM_QUEUE.popleft()
            job = _WARM_JOBS.get(key)
            if job is None:
                continue
        _warm_walk(key[0], job.get("acc_id"), job)

def _acc_name(type: str, acc_id: int | None) -> str:
    if acc_id is None:
        return f"{type} 默认账号"
    try:
        with SessionLocal() as db:
            acc = db.get(Account, int(acc_id))
    except Exception:
        return f"{type}#{acc_id}"
    if acc is None:
        return f"{type}#{acc_id}"
    return acc.alias or acc.nickname or f"{type}#{acc_id}"

def _enqueue_warm(type: str, acc_id: int | None) -> dict:
    acc_key = str(acc_id) if acc_id else "main"
    key = (type, acc_key)
    global _WARM_WORKER
    with _WARM_LOCK:
        job = _WARM_JOBS.get(key)
        if job and job["status"] in ("queued", "running"):
            return job
        _WARM_JOBS[key] = {
            "type": type, "acc_id": acc_id, "acc_key": acc_key, "acc_name": _acc_name(type, acc_id),
            "status": "queued", "done": 0, "total": 1, "message": "",
        }
        _WARM_QUEUE.append(key)
        if _WARM_WORKER is None or not _WARM_WORKER.is_alive():
            _WARM_WORKER = threading.Thread(target=_warm_worker, daemon=True, name="warm-worker")
            _WARM_WORKER.start()
        return _WARM_JOBS[key]

@router.post("/cache/trees/warm")
def warm_trees(body: dict, _user=CurrentUser):
    type = body.get("type") or ""
    if type not in WARM_TYPES:
        raise HTTPException(status_code=400, detail=f"网盘 {type} 暂不支持目录预热")
    acc_id = body.get("acc_id")
    job = _enqueue_warm(type, int(acc_id) if acc_id else None)
    return {**job}

@router.get("/cache/trees/warm/status")
def warm_status(type: str, acc_id: int | None = None, _user=CurrentUser):
    acc_key = str(acc_id) if acc_id else "main"
    job = _WARM_JOBS.get((type, acc_key))
    return {**job} if job else {"type": type, "status": "idle", "done": 0, "total": 0, "message": ""}

@router.post("/cache/trees/warm-all")
def warm_all(_user=CurrentUser):
    with SessionLocal() as db:
        rows = db.query(Account).filter(Account.status == "connected").order_by(Account.id).all()
        targets = [(r.type, r.id) for r in rows if r.type in WARM_TYPES]
    for type, acc_id in targets:
        _enqueue_warm(type, acc_id)
    return {"count": len(targets)}

@router.get("/cache/trees/warm-all/status")
def warm_all_status(_user=CurrentUser):
    with _WARM_LOCK:
        jobs = [dict(v) for v in _WARM_JOBS.values()]
    return {
        "total_jobs": len(jobs),
        "done_jobs": sum(1 for j in jobs if j["status"] in ("done", "error")),
        "failed_jobs": sum(1 for j in jobs if j["status"] == "error"),
        "queued": sum(1 for j in jobs if j["status"] == "queued"),
        "current": next((j for j in jobs if j["status"] == "running"), None),
        "jobs": jobs,
    }

def _resolve_path(adapter, path: str) -> str:
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
    if parent in ("0", ""):
        base = (parent_path or "/").rstrip("/")
    else:
        with SessionLocal() as db:
            prow = (
                db.query(DirPathCache)
                .filter(DirPathCache.account_type == drive_type, DirPathCache.dir_id == parent)
                .first()
            )
        if prow is None or not prow.dir_path:
            return
        base = prow.dir_path.rstrip("/")
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

from pydantic import BaseModel

class DirCreateBody(BaseModel):
    type: str
    acc_id: int | None = None
    parent_path: str
    parent_fid: str = ""
    cache_key: str = ""
    name: str

class DirRenameBody(BaseModel):
    type: str
    acc_id: int | None = None
    path: str
    fid: str = ""
    cache_key: str = ""
    new_name: str

class DirDeleteBody(BaseModel):
    type: str
    acc_id: int | None = None
    path: str
    fid: str = ""
    cache_key: str = ""

def _acc_key(acc_id: int | None) -> str:
    return str(acc_id) if acc_id else "main"

def _dir_op_guard(type: str, name: str = "") -> None:
    if type not in ("baidu", "quark", "115"):
        raise HTTPException(status_code=400, detail=f"未知网盘 {type}")
    if name and ("/" in name or "\\" in name or name in (".", "..")):
        raise HTTPException(status_code=400, detail="名称不能包含路径分隔符")

def _purge_path_cache(type: str, fid: str, old_path: str) -> None:
    prefix = old_path.rstrip("/") + "/"
    with SessionLocal() as db:
        if fid:
            row = db.get(DirPathCache, (type, fid))
            if row is not None:
                db.delete(row)
        like = prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        db.query(DirPathCache).filter(
            DirPathCache.account_type == type,
            DirPathCache.dir_path.like(like + "%", escape="\\"),
        ).delete(synchronize_session=False)
        db.commit()

@router.post("/files/dir")
def create_dir(body: DirCreateBody, _user=CurrentUser):
    _dir_op_guard(body.type, body.name)
    parent_path = body.parent_path.rstrip("/") or "/"
    acc_key = _acc_key(body.acc_id)
    with SessionLocal() as db:
        adapter = make_adapter_for(db, body.type, body.acc_id)
    if body.type == "quark":
        pfid = body.parent_fid or _resolve_path(adapter, parent_path)
        fid = adapter.create_dir(pfid, body.name)

        with SessionLocal() as db:
            db.add(
                DirPathCache(
                    account_type="quark",
                    dir_id=fid,
                    dir_path=(parent_path.rstrip("/") or "") + "/" + body.name,
                    parent_id=pfid,
                    last_seen_at=int(time.time()),
                )
            )
            db.commit()
    elif body.type == "baidu":
        fid = adapter.create_dir(parent_path, body.name)
    elif body.type == "115":
        pfid = body.parent_fid or adapter.path_to_cid(parent_path)
        fid = adapter.create_dir(pfid, body.name)

        with SessionLocal() as db:
            db.add(
                DirPathCache(
                    account_type="115",
                    dir_id=fid,
                    dir_path=(parent_path.rstrip("/") or "") + "/" + body.name,
                    parent_id=pfid,
                    last_seen_at=int(time.time()),
                )
            )
            db.commit()
    else:
        raise HTTPException(status_code=400, detail=f"网盘 {body.type} 的建目录尚未实现")
    full = (parent_path.rstrip("/") or "") + "/" + body.name
    dir_cache.update(
        (body.type, acc_key, body.cache_key),
        lambda items: (items or []) + [{"fid": fid, "name": body.name, "is_dir": True, "size": 0}],
    )
    return {"fid": fid, "path": full}

@router.post("/files/dir/rename")
def rename_dir(body: DirRenameBody, _user=CurrentUser):
    _dir_op_guard(body.type, body.new_name)
    acc_key = _acc_key(body.acc_id)
    with SessionLocal() as db:
        adapter = make_adapter_for(db, body.type, body.acc_id)
    if body.type == "quark":
        fid = body.fid or _resolve_path(adapter, body.path)
        adapter.rename_dir(fid, body.new_name)
        _purge_path_cache("quark", fid, body.path)

        new_full = body.path.rstrip("/").rsplit("/", 1)[0]
        new_full = (new_full + "/" + body.new_name) if new_full else "/" + body.new_name
        with SessionLocal() as db:
            db.add(
                DirPathCache(
                    account_type="quark",
                    dir_id=fid,
                    dir_path=new_full,
                    parent_id="",
                    last_seen_at=int(time.time()),
                )
            )
            db.commit()
    elif body.type == "115":
        fid = body.fid or adapter.path_to_cid(body.path)
        adapter.rename_dir(fid, body.new_name)
        _purge_path_cache("115", fid, body.path)

        new_full = body.path.rstrip("/").rsplit("/", 1)[0]
        new_full = (new_full + "/" + body.new_name) if new_full else "/" + body.new_name
        with SessionLocal() as db:
            db.add(
                DirPathCache(
                    account_type="115",
                    dir_id=fid,
                    dir_path=new_full,
                    parent_id="",
                    last_seen_at=int(time.time()),
                )
            )
            db.commit()
    elif body.type == "baidu":
        full = adapter.rename_dir(body.path, body.new_name)
    else:
        raise HTTPException(status_code=400, detail=f"网盘 {body.type} 的重命名尚未实现")
    old_fid = body.fid or (body.path if body.type == "baidu" else "")
    new_fid = full if body.type == "baidu" else (body.fid or new_full)
    dir_cache.update(
        (body.type, acc_key, body.cache_key),
        lambda items: [it for it in (items or []) if it.get("fid") != old_fid]
        + [{"fid": new_fid, "name": body.new_name, "is_dir": True, "size": 0}],
    )
    return {"fid": new_fid, "path": full if body.type == "baidu" else new_full}

@router.post("/files/dir/delete")
def delete_dir(body: DirDeleteBody, _user=CurrentUser):
    _dir_op_guard(body.type)
    acc_key = _acc_key(body.acc_id)
    with SessionLocal() as db:
        adapter = make_adapter_for(db, body.type, body.acc_id)
    if body.type == "quark":
        fid = body.fid or _resolve_path(adapter, body.path)
        adapter.delete_dir(fid)
        _purge_path_cache("quark", fid, body.path)
    elif body.type == "115":
        fid = body.fid or adapter.path_to_cid(body.path)
        adapter.delete_dir(fid)
        _purge_path_cache("115", fid, body.path)
    elif body.type == "baidu":
        adapter.delete_dir(body.path)
        fid = body.path
    else:
        raise HTTPException(status_code=400, detail=f"网盘 {body.type} 的删除尚未实现")
    dir_cache.update(
        (body.type, acc_key, body.cache_key),
        lambda items: [it for it in (items or []) if it.get("fid") != fid],
    )
    return {"ok": True}
