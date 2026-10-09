"""缓存配置（目录树缓存策略 + 内存观测）与目录浏览（转存弹窗懒加载）。"""
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
    # 水位条语义：已用缓存字节 / 设置的缓存大小上限（MB）
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


@router.post("/cache/trees/{key:path}/refresh")
def refresh_tree(key: str, _user=CurrentUser):
    """行内「刷新」：**强制实拉网盘并回写缓存**（不是只失效——失效=下次浏览才重拉，
    那是「清除」的语义，两者此前共用一个函数，刷新成了换皮的清除，2026-10-08 用户发现）。

    key 形如 "quark/main/0"（type/account/cid）。
    ⚠️ 必须用 :path 转换器——key 本身含斜杠，默认 {key} 只匹配单段，
    "baidu/main/0" 一律 404（行内刷新/清除点了没反应的根因，2026-10-04）。"""
    parts = key.split("/", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="key 格式应为 type/account/cid")
    type_, acc, cid = parts
    acc_id = int(acc) if acc.isdigit() else None  # "main" = 该类型默认账号
    # 键形状分两种（与 list_files 的落键一致）：根层键是**路径**（/1.影视、/），子层键是 fid——
    # 路径键必须走 parent='0'+path 的解析分支，fid 键走 parent 直列，拿错分支实拉必报错
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
    """行内「清除」：只把缓存条目丢掉（下次浏览该目录时才实拉），不主动打网盘。"""
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
    return {"count": dir_cache.clear()}  # 全部失效 = 下次浏览直连重拉


@router.get("/files/list")
def list_files(type: str = "quark", parent: str = "0", path: str = "", force_refresh: bool = False, acc_id: int | None = None, _user=CurrentUser):
    """转存弹窗目录浏览：按父目录拉一层，缓存 key=(type, account, parent)。

    acc_id 空 = 该类型默认账号（缓存键记作 "main"）；指定账号则键里带账号 id，
    同一网盘不同账号的目录缓存互不串。"""
    acc_key = str(acc_id) if acc_id else "main"
    # 键统一用目录本身：path 解析模式（parent='0' 带 path）与子层展开（parent=目录路径）
    # 必须落在同一个键上——百度就用路径当目录标识，真根的键是 "0"，不会撞。
    # 之前用 "p:"+path 当键，初始化浏览与子层展开互不命中，同一目录缓存了两份还各自 miss。
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
        # 风控/限频回 429：前端见到 429 一次都不重试（盲目重试=越打封越久，2026-10-07 实锤）
        raise HTTPException(status_code=429, detail=str(e))
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # cached=False = 这层真打了网盘（前端据此决定下一次列目录前是否要风控节流）
    return {"cached": bool(flag.get("cached")), "items": items}


def _load_dir_payload(type: str, acc_id: int | None, parent: str, path: str):
    """拉取并映射一层目录（/files/list 与全树预热共用）。调用方负责包进 dir_cache.get_or_load。"""
    with SessionLocal() as db:
        adapter = make_adapter_for(db, type, acc_id)
    if type == "115":
        # 115 与 quark 同款用 cid 当目录标识：根层按路径解析（根=0），子层 parent 就是 cid
        cid = adapter.path_to_cid(path) if (path and parent in ("0", "")) else parent
        rows = adapter._list_own_dir(cid)
        out = [
            {
                "fid": str(r.get("fid") or r.get("cid") or ""),
                "name": str(r.get("n") or r.get("fn") or ""),
                "is_dir": not (r.get("sha") or r.get("sha1")),  # 文件条目带哈希字段，目录没有
                "size": int(r.get("s") or 0),
            }
            for r in rows
        ]
        _remember_paths("115", parent, path, out)
        return out
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


# ===== 全树预热：把目录树灌进缓存。**全局单工队列** =====
# 上限防失控：目录数/深度到顶就停，剩余的浏览时按需缓存
WARM_MAX_DIRS = 300
WARM_MAX_DEPTH = 5
# 任务键 (type, acc_key)；**只有一个 worker 线程按序消费**——同网盘多账号并行预热
# 就是拿几个账号同时撞同一家风控（2026-10-07 用户定稿：进队列串行跑）
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
    except Exception as e:  # noqa: BLE001 —— 预热失败不影响业务，状态报给前端
        job["status"] = "error"
        job["message"] = str(e)


def _warm_worker() -> None:
    """单工消费线程：一次只跑一个预热任务，跑完才取下一个（跨网盘也串行——
    不同的盘并行虽不共享风控，但没必要赶时间，慢即是稳）。"""
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
    """账号展示名（预热进度里说清当前在跑谁）：别名 > 昵称 > 类型#id。"""
    if acc_id is None:
        return f"{type} 默认账号"
    try:
        with SessionLocal() as db:
            acc = db.get(Account, int(acc_id))
    except Exception:  # noqa: BLE001
        return f"{type}#{acc_id}"
    if acc is None:
        return f"{type}#{acc_id}"
    return acc.alias or acc.nickname or f"{type}#{acc_id}"


def _enqueue_warm(type: str, acc_id: int | None) -> dict:
    """入队一个预热任务；同 (type, 账号) 已在排队/在跑则直接回现状（不重复排）。"""
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
    """后台入队某网盘的全树预热（acc_id 空 = 该类型默认账号），立即返回。
    重复调用时若同账号已在排队/在跑则直接回当前进度。"""
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
    """全部已连接账号入队预热（单工队列串行跑，同网盘多账号绝不并行——防风控）。"""
    with SessionLocal() as db:
        rows = db.query(Account).filter(Account.status == "connected").order_by(Account.id).all()
        targets = [(r.type, r.id) for r in rows if r.type in WARM_TYPES]
    for type, acc_id in targets:
        _enqueue_warm(type, acc_id)
    return {"count": len(targets)}


@router.get("/cache/trees/warm-all/status")
def warm_all_status(_user=CurrentUser):
    """预热队列总览：排队数 / 在跑的当前任务 / 已完成任务数（缓存配置页轮询用）。"""
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
    """浏览时顺手 reconcile 目录映射表。

    父目录的**真实路径**是本表Correctness的命门：根层用 path 参数；子层（parent=fid、
    path=''）必须反查父 fid 的已记路径——查不到就整批放弃记录。曾按空串拼路径把
    深层子目录全记成根路径（'/配方' 之类 470 行污染），路径解析顺着错行指到
    套娃目录，浏览树只剩 1 个目录、路径显示成 1.影视/1.影视/…（2026-10-03）。"""
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
            return  # 父路径未知，宁缺勿错
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


# ===== 目录管理（浏览弹窗的新建/重命名/删除，2026-10-03） =====
# 三个操作做完都在后端**就地更新对应层的目录缓存**（dir_cache.update，不整层回源），
# 前端也同步改本地树节点——全程不重打列目录接口。夸克额外维护 fid→路径 映射表：
# 改名/删除把旧路径的映射行清掉（含子树前缀），下次浏览自然重建，别给解析留毒。

from pydantic import BaseModel


class DirCreateBody(BaseModel):
    type: str
    acc_id: int | None = None
    parent_path: str  # 父目录完整路径
    parent_fid: str = ""  # quark：父目录 fid（树节点上带）；空则后端按路径解析
    cache_key: str = ""  # 父层在目录缓存里的 key（前端知道自己是按哪层拉的）
    name: str


class DirRenameBody(BaseModel):
    type: str
    acc_id: int | None = None
    path: str  # 旧完整路径
    fid: str = ""  # quark：目录 fid
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
    """删掉某目录的映射行 + 其子树所有行（按旧路径前缀匹配）。改名/删除后调用。"""
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
        # 映射表：新目录 fid → 父路径/新名
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
        # 映射表：新目录 cid → 父路径/新名（同 quark）
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
        # 改名后的新映射行（浏览到该层时会补子树，这里先补自己）
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
        # 改名后的新映射行（同 quark）
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
