"""搜索：pansou 代理 + 频道/地址展示。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..services import pansou

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/results")
def search_results(kw: str, refresh: bool = False, _user=CurrentUser):
    if not kw.strip():
        return []
    try:
        return pansou.search(kw.strip(), refresh=refresh)
    except pansou.PanSouError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/pansou-addr")
def pansou_addr(_user=CurrentUser):
    from ..services.settings_svc import get_group

    return get_group("settings")["search"]["pansou_url"]


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
