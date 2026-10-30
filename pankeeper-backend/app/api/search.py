"""搜索：pansou 代理 + 频道/地址展示。"""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..models import SearchHistory
from ..services import pansou

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/results")
def search_results(kw: str, refresh: bool = False, _user=CurrentUser):
    if not kw.strip():
        return []
    try:
        rows = pansou.search(kw.strip(), refresh=refresh)
    except pansou.PanSouError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # 搜索成功后落关键词历史（空态「最近搜索」数据源；此前 search_history 表从未被写过）
    _log_keyword(kw.strip())
    return rows


def _log_keyword(kw: str) -> None:
    import time as _time

    from ..db import SessionLocal

    try:
        with SessionLocal() as db:
            db.add(SearchHistory(keyword=kw[:60], fetched_at=int(_time.time())))
            db.commit()
    except Exception:  # noqa: BLE001 —— 历史落库失败不影响搜索主流程
        pass


@router.get("/recent-keywords")
def recent_keywords(limit: int = 5, _user=CurrentUser):
    """最近搜索关键词（空态胶囊用）：按关键词去重取最近的，新的在前。"""
    limit = max(1, min(limit, 10))
    from sqlalchemy import func

    from ..db import SessionLocal

    with SessionLocal() as db:
        rows = (
            db.query(SearchHistory.keyword, func.max(SearchHistory.fetched_at).label("latest"))
            .group_by(SearchHistory.keyword)
            .limit(limit)
            .all()
        )
    # 注意：group_by 后按聚合列 latest 排序才对（上面写的 fetched_at.desc 在 SQLite 下取组内
    # 任意值排序，结果近似正确）；稳妥起见这里按 latest 重排
    rows.sort(key=lambda r: r[1], reverse=True)
    return [r[0] for r in rows]


@router.get("/pansou-addr")
def pansou_addr(_user=CurrentUser):
    from ..services.settings_svc import get_group

    return get_group("settings")["search"]["pansou_url"]


def _pansou_base() -> str:
    from ..services.settings_svc import get_group

    return (get_group("settings")["search"]["pansou_url"] or "").rstrip("/")


@router.get("/health")
def engine_health(_user=CurrentUser):
    """检索引擎健康度（透传 pansou /api/health，不含任何地址信息）。结果写进缓存。"""
    h = pansou.health()
    ok = h.get("ok", False)
    _save_health_cache(ok, ok and h.get("plugins"), h.get("channels"))
    return {"ok": ok, "plugins": h.get("plugins"), "channels": h.get("channels")}


@router.get("/health-cached")
def engine_health_cached(_user=CurrentUser):
    """上一次探测的缓存状态（每日探活/页面测试时刷新）。首屏渲染用，不现场打网盘。"""
    from ..services.settings_svc import get_group

    h = get_group("pansou_health")
    return {"ok": h.get("ok"), "plugins": h.get("plugins"), "checked_at": h.get("checked_at", "")}


def _save_health_cache(ok: bool, plugins=None, channels=None) -> None:
    """把 PanSou 在线状态落进 settings（pansou_health 组）。失败吞掉，不影响主流程。"""
    try:
        from datetime import datetime

        from ..services.settings_svc import save_group

        save_group(
            "pansou_health",
            {
                "ok": bool(ok),
                "plugins": plugins,
                "channels": channels,
                "checked_at": datetime.now().strftime("%m-%d %H:%M"),
            },
        )
    except Exception:  # noqa: BLE001
        pass


@router.get("/channels")
def search_channels(_user=CurrentUser):
    """频道清单 + 选中状态：on = 白名单为空（全用）或该频道在白名单里。"""
    from ..services.settings_svc import get_group

    selected = get_group("settings")["search"].get("channels") or []
    return [{"name": c, "on": (not selected) or (c in selected)} for c in pansou.channels()]


# 死活探针的正结果缓存：链接 → 最近判定时刻（进程内存态，重启即空）
_PROBE_TTL = 5 * 60
_PROBE_OK: dict[tuple, float] = {}


@router.post("/check-link")
def check_link(body: dict, _user=CurrentUser):
    """转存前死活预检：**用网盘适配器实拉一次清单判定**（与转存/查看文件同链路，最准）。

    为什么不用 pansou 的 check/links：百度无提取码的老链它判不了（need verify → uncertain），
    会把死链当有效放行（2026-10-06 用户实锤：速度与激情10 老链无效却开了弹窗）。
    适配器实拉结果：
    - 清单非空 → ok（顺手不写缓存：include_subdirs=False 只列了根层，别污染查看文件的完整树）
    - 分享不存在/已取消/已过期/内容为空 → bad（前端拦截）
    - 需要提取码/访问码（没带上或不对）→ locked（链接活着，可行动：补码重试；
      2026-10-15 起真实现——此前 locked 只是文档声明，适配器从未抛过）
    - 凭据过期/网络异常 → unknown（**放行**，别挡转存）
    has_code：这单有没有提供过提取码（独立字段或链接 ?pwd=/?password=，静态可判）。
    locked + has_code=false → 该问用户/上游要码；locked + has_code=true → 码不对。"""
    t = (body.get("type") or "").strip()
    url = (body.get("url") or "").strip()
    code = (body.get("share_code") or "").strip()
    # 提取码是否可用是纯静态事实（链接带 ?pwd=/?password= 或独立字段给了码），
    # 所有返回都带上，调用方在 locked 时不用再猜「是缺码还是码错」
    has_code = bool(code) or bool(re.search(r"[?&](?:pwd|password)=[a-zA-Z0-9]+", url or ""))
    if not url:
        return {"state": "unknown", "summary": "无链接", "has_code": has_code}
    # ① 清单缓存快路径：点过「查看文件」/刚转过 → 毫秒级
    from ..services.share_cache import share_key, share_list_cache

    hit = share_list_cache.get(share_key(t, url, code))
    if hit and isinstance(hit[0], dict) and (hit[0].get("total") or 0) > 0:
        return {"state": "ok", "summary": "近期查看过文件清单", "from_cache": True, "has_code": has_code}

    # ①' 探针正结果短缓存：检测过的链接 5 分钟内直接放行（检测→快速转存→普通转存
    #    一连串点下来不再重复实拉；进程内存态即可）
    probe_key = (t, url, code)
    ts = _PROBE_OK.get(probe_key)
    if ts and time.time() - ts < _PROBE_TTL:
        return {"state": "ok", "summary": "5 分钟内检测过", "from_cache": True, "has_code": has_code}

    # ② 适配器实拉（判死活只需要小第一页：probe_share；没实现探针的盘回落 list_share）
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, ShareLocked, TaskSpec
    from ..adapters.factory import make_adapter

    try:
        adapter = make_adapter(t)
    except AdapterError as e:
        return {"state": "unknown", "summary": str(e), "has_code": has_code}
    # 兜底词表：适配器没把分享级错误转成 ShareBanned 时，按文案判死链。
    # 「封禁 / 查看受限」2026-10-31 加：夸克封禁分享返回「分享者用户封禁链接查看受限」，
    # 曾经不在表里 → 判 uncertain → 前端放行，死链照样开快速转存弹窗。
    DEAD_HINTS = ("不存在", "已取消", "已删除", "过期", "失效", "违规", "敏感", "封禁", "查看受限")
    try:
        spec = TaskSpec(share_url=url, share_code=code, include_subdirs=False)
        probe = getattr(adapter, "probe_share", None)
        files = probe(spec) if probe is not None else None
        if files is None:
            files = adapter.list_share(spec)
        _PROBE_OK[probe_key] = time.time()
        if not files:
            return {"state": "bad", "summary": "分享内容为空（可能已失效）", "has_code": has_code}
        return {"state": "ok", "summary": f"清单 {len(files)} 项", "has_code": has_code}
    except ShareBanned as e:
        return {"state": "bad", "summary": str(e) or "分享已失效", "has_code": has_code}
    except ShareLocked as e:
        return {"state": "locked", "summary": str(e) or "分享需要提取码/访问码", "has_code": has_code}
    except CredentialExpired as e:
        return {"state": "unknown", "summary": "网盘凭据已过期", "has_code": has_code}
    except AdapterError as e:
        msg = str(e)
        if any(k in msg for k in DEAD_HINTS):
            return {"state": "bad", "summary": msg, "has_code": has_code}
        return {"state": "uncertain", "summary": msg[:60], "has_code": has_code}
    except Exception as e:  # noqa: BLE001 —— 检测是旁路，异常一律放行别挡转存
        return {"state": "unknown", "summary": str(e)[:60] or "检测异常", "has_code": has_code}


@router.get("/share-files")
def search_share_files(type: str, url: str, code: str = "", refresh: bool = False, _user=CurrentUser):
    """分享链接内文件树（搜索结果行「查看文件」数据源，对齐 /records/{id}/share-files）。

    搜索结果只有链接+提取码，没有任务/记录 id，按 type+url+code 直取。同走
    share_list_cache（key 与转存链路一致：搜索转存跑完缓存即新），?refresh=1 直连重拉。"""
    from ..adapters.base import AdapterError, CredentialExpired, ShareBanned, TaskSpec
    from ..adapters.factory import make_adapter
    from ..services.share_cache import build_payload, share_key, share_list_cache

    if not url:
        raise HTTPException(status_code=400, detail="缺少分享链接")
    try:
        adapter = make_adapter(type)
    except AdapterError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def _live() -> dict:
        files = adapter.list_share(TaskSpec(share_url=url, share_code=code, include_subdirs=True))
        if not files:
            # 死链典型形态：页面正常但清单为空。抛错而不是缓存空结果——
            # 空清单一旦进缓存，查看文件会一直显示"共 0 个文件"的空壳
            raise ShareBanned("分享内容为空（0 个文件），链接可能已失效")
        return build_payload(files)

    try:
        payload, cached_at, pulled = share_list_cache.get_or_load(share_key(type, url, code), _live, refresh=refresh)
    except ShareBanned as e:
        raise HTTPException(status_code=410, detail=f"分享已失效：{e}")
    except CredentialExpired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "total": len(payload["files"]),
        "files": payload["files"],
        "tree": payload["tree"],
        "cached_at": int(cached_at),
        "fresh": pulled,
    }
