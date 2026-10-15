"""AI 专用接口层：watch-movie 技能等 AI 调用方的**唯一**入口。

与既有 /api/* 路由的关系：**复用不复制**——内部 import 各路由模块的处理函数 /
service 函数，外面套一层 AI 判断闸（2026-10-15 定案）：
- API Key 只能调 /api/ai/*（auth_guard 强制，打其他口子一律 403，含 /docs）；
- JWT 登录态 / 本机回环不受限（本地调试两套接口行为一致）。

为什么入队不直通 POST /api/queue/tasks 而要单走 /transfer：前端「探活通过才能
点转存」是两道 UI 闸（行级 check-link + 弹窗内拉文件清单失败禁按钮），AI 直调
入队接口会全部绕过——坏链/缺提取码任务进队列 → 执行失败 → 每次失败必推一条
通知（2026-10-15 手机被失败推送轰炸的根因）。transfer 把这两道闸下沉为服务端
强制：探活不过 / 缺码 / 勾选文件不存在，一律 422 拒绝，**不入队、不推送**。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import CurrentUser

router = APIRouter(prefix="/api/ai", tags=["pk-ai"])


def _reject(reason: str, message: str) -> None:
    """判断闸统一拒绝：422 + {reason, message}。reason 供调用方（pk.py/SKILL.md）分支：
    need_code=缺提取码/码错（可行动：要码重试）；bad_link=死链（换候选）；
    no_dd/bad_path/bad_acc/no_rename=配置或参数问题（停下找用户）；
    file_missing=勾选文件不在清单里。"""
    raise HTTPException(status_code=422, detail={"reason": reason, "message": message})


# ---------------------------------------------------------------- 只读：环境与语境

@router.get("/preflight")
def preflight(_user=CurrentUser):
    """前置检查一次拿全：账号 + 该类型转存配置目录（替代原 pk.py 的 2+N 次调用）。

    drives[] 与 SKILL.md 口径一致：status 非 connected 的盘报告用户、不参与转存；
    全部 dd_configured=false → 停，让用户先去「转存配置」配目录。"""
    from ..api.accounts import _default_map, _is_first_of_type
    from ..db import SessionLocal
    from ..models import Account, DdItem

    with SessionLocal() as db:
        dmap = _default_map(db)
        accs = db.query(Account).order_by(Account.type, Account.id).all()
        items = db.query(DdItem).all()
        drives = []
        for acc in accs:
            paths = sorted({it.path for it in items if it.type == acc.type and it.path})
            explicit = dmap.get(acc.type)
            drives.append({
                "type": acc.type,
                "acc_id": acc.id,
                "alias": acc.alias,
                "status": acc.status,
                "is_default": (acc.id == explicit) if explicit else _is_first_of_type(acc),
                "dd_configured": bool(paths) or any(it.type == acc.type for it in items),
                "dd_paths": paths,
            })
    return {"drives": drives}


@router.get("/history")
def history(limit: int = 10, _user=CurrentUser):
    """最近搜索词 + 手动转存记录（语境判断用，不打网盘）。"""
    from ..api.records import list_records
    from ..api.search import recent_keywords

    kws = recent_keywords(limit=max(1, min(limit, 10)))
    recs = list_records(source="search")
    rows = [
        {"id": r["id"], "name": r["n"], "type": r["t"], "path": r["p"], "status": r["st"], "time": r["tm"]}
        for r in recs[: max(1, min(limit, 50))]
    ]
    return {"recent_keywords": kws, "recent_transfers": rows}


@router.get("/queue-state")
def queue_state(_user=CurrentUser):
    """队列快照（裁剪版）：去掉 logs 等内部大字段，AI 只关心排到哪了。"""
    from ..queue.engine import get_engine

    snap = get_engine().state_public()
    keep = ("id", "name", "type", "status", "phase", "progress", "files", "size",
            "path", "rename", "doneAt", "shareUrl")
    return {
        "seq": snap.get("seq"),
        "tasks": [{k: t.get(k) for k in keep} for t in snap.get("tasks") or []],
    }


@router.get("/notify-channel")
def notify_channel(_user=CurrentUser):
    """用户配置的通知通道（汇报「结果看 xx 推送」用）。只回通道名与是否配好，
    绝不回 sendkey/device_key 本体。"""
    from ..services.settings_svc import get_group

    n = get_group("settings").get("notify") or {}
    if not n.get("enabled"):
        return {"ok": False, "channel": None, "label": None, "reason": "通知总开关未开启"}
    labels = {"serverchan": "Server酱", "bark": "Bark", "webhook": "Webhook"}
    key_fields = {"serverchan": "sendkey", "bark": "device_key", "webhook": "url"}
    ch = (n.get("channel") or "").strip()
    return {
        "ok": True,
        "channel": ch,
        "label": labels.get(ch, ch or "未知"),
        "configured": bool((n.get(ch) or {}).get(key_fields.get(ch, ""))),
    }


# ---------------------------------------------------------------- 打网盘：搜索 / 识别 / 探活 / 清单

@router.get("/search")
def search(kw: str, refresh: bool = False, drives: str = "", _user=CurrentUser):
    """搜资源（pansou 代理）。与前端搜索框同一个内部实现（search.search_results），
    会落搜索关键词历史。

    drives=baidu,quark,115（逗号分隔，AI 从 preflight 拿）→ 服务端过滤分组：
    storable=已配置盘的候选（只从这里挑），magnets=磁力链接（留着，没得存时问用户），
    其余未知盘丢弃只计数。不传 drives → 原样返回全量列表（调试用）。"""
    from ..api.search import search_results

    rows = search_results(kw=kw, refresh=refresh)
    keep = {d.strip() for d in drives.split(",") if d.strip()}
    if not keep:
        return rows
    storable = [r for r in rows if r.get("t") in keep]
    magnets = [r for r in rows if r.get("t") == "magnet"]
    return {
        "kw": kw,
        "drives": sorted(keep),
        "storable": storable,
        "magnets": magnets,
        "dropped": len(rows) - len(storable) - len(magnets),
    }


@router.post("/recognize")
def recognize(body: dict, _user=CurrentUser):
    """识别片名 → TMDB 候选（= 前端「识别」按钮）。body: {name, hint?, share_type?,
    share_url?, share_code?}。"""
    from ..api.recognize import RecognizeBody as _Body
    from ..api.recognize import recognize as _recognize

    return _recognize(_Body(
        name=(body.get("name") or "").strip(),
        hint=(body.get("hint") or "").strip(),
        share_type=(body.get("share_type") or "").strip(),
        share_url=(body.get("share_url") or "").strip(),
        share_code=(body.get("share_code") or "").strip(),
    ))


@router.post("/check")
def check(body: dict, _user=CurrentUser):
    """死活预检。body: {type, url, share_code?}。返回 {state: ok|bad|locked|uncertain|
    unknown, summary, has_code}——locked=链接活着但要提取码（has_code=false 该问用户
    要码；has_code=true 是码不对）。"""
    from ..api.search import check_link

    return check_link({
        "type": (body.get("type") or "").strip(),
        "url": (body.get("url") or "").strip(),
        "share_code": (body.get("share_code") or "").strip(),
    })


@router.get("/files")
def files(type: str, url: str, code: str = "", refresh: bool = False, _user=CurrentUser):
    """分享内文件清单（扁平 list，AI 挑正片/勾 file_paths 用；树砍掉省流量）。"""
    from ..api.search import search_share_files

    payload = search_share_files(type=type, url=url, code=code, refresh=refresh)
    payload.pop("tree", None)
    return payload


# ---------------------------------------------------------------- 入队：transfer 判断闸

class TransferBody(BaseModel):
    """与前端快速转存确认按钮同语义（with_shell + rename 必带），字段对齐
    queue_api.EnqueueBody 的子集——AI 不开放 files/size/qms_id/strm_id/lp_event。"""

    name: str
    type: str = "quark"
    acc_id: int | None = None
    path: str = "/"
    share_url: str = ""
    share_code: str = ""
    include_subdirs: bool = True
    file_paths: list[str] | None = None
    rename: str = ""
    with_shell: bool = False


@router.post("/transfer")
def transfer(body: TransferBody, _user=CurrentUser):
    """AI 一键转存（唯一入队口）：判断闸全过才 engine.enqueue，任何一闸不过都是
    422 + {reason, message}，**不入队、不产生失败推送**。

    闸序：① 参数/配置（type 配过转存目录、path∈dd_paths、acc_id 属该类型、
    建壳必须带 rename）→ ② 探活（bad=死链 / locked=缺码，直接拒）→ ③ 实拉清单
    （share_list_cache 联动：AI 先调过 files 的毫秒级命中；ShareLocked 在此显形）
    → ④ file_paths 逐条必须在清单内 → ⑤ 入队返回 {pos}（-1=已在队列，非失败）。"""
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, ShareLocked, TaskSpec
    from ..adapters.factory import make_adapter
    from ..api.search import check_link
    from ..db import SessionLocal
    from ..models import Account, DdItem
    from ..queue.engine import get_engine
    from ..services.share_cache import build_payload, share_key, share_list_cache

    t = body.type.strip()
    url = body.share_url.strip()
    code = body.share_code.strip()
    rename = body.rename.strip()
    if not url:
        _reject("bad_request", "缺少 share_url")
    if not body.name.strip():
        _reject("bad_request", "缺少 name")

    # ① 参数/配置闸
    with SessionLocal() as db:
        dd_paths = sorted({
            r.path for r in db.query(DdItem).filter(DdItem.type == t).all() if r.path
        })
        acc_row = db.get(Account, body.acc_id) if body.acc_id is not None else None
    if not dd_paths:
        _reject("no_dd", f"网盘 {t} 还没配转存目录（先去 pankeeper「转存配置」配好再转）")
    if body.path not in dd_paths:
        _reject("bad_path", f"path 必须是 {t} 已配置的转存目录之一：{dd_paths}")
    if acc_row is not None and acc_row.type != t:
        _reject("bad_acc", f"acc_id={body.acc_id} 不是 {t} 的账号")
    if body.with_shell and not rename:
        _reject("no_rename", "with_shell=true 必须带 rename（壳文件夹名，QMS 靠它刮削）")

    # ② 探活闸（check_link 内部有两层缓存，流程里先调过 /check 的这里是毫秒级）
    probe = check_link({"type": t, "url": url, "share_code": code})
    state = probe.get("state")
    if state == "bad":
        _reject("bad_link", f"链接已失效：{probe.get('summary') or '检测未通过'}")
    if state == "locked":
        msg = f"分享需要提取码/访问码：{probe.get('summary') or '链接带码校验'}"
        if not probe.get("has_code"):
            msg += "；本次未提供提取码——向用户要码后带 share_code 重新走 check/files/transfer"
        _reject("need_code", msg)

    # ③ 清单闸：真拉一次完整树（缓存联动），缺码/死链在这一步显形
    def _live() -> dict:
        adapter = make_adapter(t)
        files = adapter.list_share(TaskSpec(share_url=url, share_code=code, include_subdirs=body.include_subdirs))
        if not files:
            raise ShareBanned("分享内容为空（0 个文件），链接可能已失效")
        return build_payload(files)

    try:
        payload, _cached_at, _pulled = share_list_cache.get_or_load(share_key(t, url, code), _live, refresh=False)
    except ShareBanned as e:
        _reject("bad_link", f"分享已失效：{e}")
    except ShareLocked as e:
        _reject("need_code", f"分享需要提取码/访问码：{e}")
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail={"reason": "cred_expired", "message": str(e)})
    except AdapterError as e:
        raise HTTPException(status_code=502, detail={"reason": "adapter_error", "message": str(e)})

    # ④ 勾选闸：file_paths 必须都在清单里（目录条目也算，勾目录=整棵子树）
    if body.file_paths:
        flat = {e["path"]: e for e in payload["files"]}
        missing = [p for p in body.file_paths if p not in flat and p.strip("/") not in flat]
        if missing:
            _reject("file_missing", f"勾选文件不在分享清单里（先重跑 files 核对）：{missing[:5]}")

    # ⑤ 入队（字段与 POST /api/queue/tasks 同构；pos=-1=同链接已在队列，非失败）
    task = body.model_dump()
    task.update({"name": body.name.strip(), "type": t, "share_url": url, "share_code": code, "rename": rename})
    pos = get_engine().enqueue(task)
    return {"pos": pos, "state": state, "total_files": payload["total"]}


# ---------------------------------------------------------------- TMDB（服务端代理）

@router.get("/tmdb-season")
def tmdb_season(tmdb_id: int, season: int, _user=CurrentUser):
    """某季单集清单（电视节目核对集数用）。走服务端 TMDB 配置（设置页「代理配置」），
    技能侧不再需要 tmdb_api_key/代理/映射 IP。"""
    from ..services import tmdb

    eps = tmdb.tv_season(tmdb_id, season)
    if eps is None:
        raise HTTPException(status_code=502, detail={"reason": "tmdb_error",
                                                     "message": "TMDB 请求失败（服务端未配 api_key 或网络不通）"})
    return {
        "season": season,
        "episode_count": len(eps),
        "episodes": [{"episode_number": e.get("episode_number"), "name": e.get("name")} for e in eps],
    }
