"""QMS 客户端：转存完成后触发刮削 / STRM 同步。

QMS 侧有自己的详细日志与队列，这里只负责触发并记录结果快照（不轮询等它跑完，
轮询语义留给后续需要「记录页显示 QMS 执行进度」时再加）。
鉴权：X-API-Key（设置页配置）；接口事实见 docs/research/bdsavepro.md §4。
"""
from __future__ import annotations

import httpx

from ..services.settings_svc import get_group


def _client(require_enabled: bool = True, url: str | None = None, apikey: str | None = None) -> tuple[httpx.Client, str] | None:
    """构造 QMS 客户端。
    - require_enabled=False 供「测试连接」使用：测的是地址与 API Key 是否可用，
      不该被「启用联动」开关挡住。
    - url / apikey 传入时优先使用（测试正在编辑、尚未保存的值），否则用库里已保存的。
    """
    cfg = get_group("settings")["qms"]
    target = (url or cfg.get("url") or "").strip()
    if not target or (require_enabled and not cfg.get("enabled")):
        return None
    key = apikey if apikey is not None else cfg.get("apikey")
    headers = {}
    if key:
        headers["X-API-Key"] = key
    return httpx.Client(base_url=target.rstrip("/"), headers=headers, timeout=15.0), target


def trigger_scrape(qms_id: int) -> tuple[bool, str]:
    """POST /api/scrape/pathes/start {"id": N}。返回 (成功?, 消息)。"""
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.post("/api/scrape/pathes/start", json={"id": qms_id})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, ""
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()


def scrape_pathes() -> list[dict] | None:
    """GET /api/scrape/pathes：QMS 刮削路径列表（转存配置页下拉）。失败返回 None。"""
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get("/api/scrape/pathes", params={"page": 1, "pageSize": 200})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, list) else (d or {}).get("list") or []
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()


def sync_pathes() -> list[dict] | None:
    """GET /api/sync/path-list：QMS STRM 同步路径列表。失败返回 None。"""
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get("/api/sync/path-list", params={"page": 1, "page_size": 200})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, list) else (d or {}).get("list") or []
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()


def scrape_records(name: str | None = None, status: str | None = None, page_size: int = 100) -> list[dict] | None:
    """GET /api/scrape/records：查刮削记录（联动推送用——等 QMS 刮完改名再推）。

    返回 list（可能为空）；QMS 未启用/连接失败返回 None（调用方据此放弃本次轮询）。
    记录字段见 qMediaSync controllers/scrape.go：file_name/media_name/tmdb_id/status/
    new_file/season_number/episode_number/type 等。
    """
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        params: dict = {"page": 1, "pageSize": page_size}
        if name:
            params["name"] = name
        if status:
            params["status"] = status
        resp = client.get("/api/scrape/records", params=params)
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return (data.get("data") or {}).get("list") or []
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()


def clear_scrape_records(ids: list[int]) -> tuple[bool, str]:
    """DELETE /api/scrape/records?ids=1,2,3 —— 按 ID 删除刮削记录（QMS 侧源码 route 实锤）。

    为什么需要：QMS **按文件路径去重**，失败记录还在时再触发刮削它不会重新刮
    （用户实测：删掉失败记录后再点才真重刮）。这里只删我们点名的 ID，比 UI 上那个
    `POST /api/scrape/clear-failed`（清**所有**目录的失败记录）精准，不误伤别的任务。
    ids 为空直接算成功（没什么可删）。
    """
    ids = [int(i) for i in (ids or []) if i]
    if not ids:
        return True, ""
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.delete("/api/scrape/records", params={"ids": ",".join(str(i) for i in ids)})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, ""
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()


def scrape_path_status(path_id: int) -> dict | None:
    """GET /api/scrape/pathes/{id} —— 刮削路径运行状态（判断"刮完了没"用）。

    2026-10-04 实测字段：`is_running`（int：0 未运行 / 1 已入队 / 2 正在跑）、
    `is_scraping`（bool）、`updated_at`（**int 秒级时间戳**）。
    失败/未启用返回 None（调用方据此退化处理，别当"已完成"）。
    """
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get(f"/api/scrape/pathes/{path_id}")
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, dict) else None
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()


def trigger_strm(strm_id: int) -> tuple[bool, str]:
    """POST /api/sync/path/start {"id": N}（STRM 同步目录挂在 QMS 侧管理）。"""
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.post("/api/sync/path/start", json={"id": strm_id})
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, ""
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()


def get_sync_path(strm_id: int) -> dict | None:
    """GET /api/sync/path/{id} —— STRM 同步路径详情。

    定向同步（/api/sync/manual）要用它的 remote_path/local_path/account_id：
    local_path 是 STRM 落盘的根目录（云盘完整路径原样拼在后面），account_id 是
    QMS 侧的账号。失败/未启用返回 None。"""
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get(f"/api/sync/path/{strm_id}")
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            return d if isinstance(d, dict) else None
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()


def manual_sync(path_id: str, path: str, target_path: str, account_id: int, is_file: bool = False) -> tuple[bool, str]:
    """POST /api/sync/manual —— **定向同步**：QMS 建一个临时任务只扫指定云盘目录。

    QMS 源码（sync.go ManualSync）：ID=0 的临时任务（TmpSyncPath），只列这一个
    目录、生成 strm，落盘位置 = target_path + 云盘完整路径——与常规同步的布局
    天然一致。临时任务不更新 last_sync_at、不触发 Emby 刷新/关联刮削（后者
    PanKeeper 自己补）。"""
    c = _client()
    if c is None:
        return False, "QMS 未启用"
    client, _ = c
    try:
        resp = client.post(
            "/api/sync/manual",
            json={"path_id": str(path_id), "path": path, "target_path": target_path,
                  "is_file": is_file, "account_id": int(account_id)},
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            return True, str(data.get("message") or "")
        return False, str(data.get("message") or f"HTTP {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        return False, f"QMS 连接失败：{e}"
    finally:
        client.close()


def get_emby_config() -> dict | None:
    """GET /api/setting/emby-config —— QMS 里配的 Emby 地址/ApiKey（借道用）。"""
    c = _client()
    if c is None:
        return None
    client, _ = c
    try:
        resp = client.get("/api/setting/emby-config")
        data = resp.json()
        if resp.status_code == 200 and data.get("code") in (0, 200):
            d = data.get("data")
            if not isinstance(d, dict):
                return None
            # QMS 实际返回 {"config": {...}} 套了一层（2026-10-04 实测），取里层；
            # 兼容将来直接平铺的情况
            return d.get("config") if isinstance(d.get("config"), dict) else d
        return None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        client.close()


def refresh_emby_library() -> tuple[bool, str]:
    """触发 Emby 刷新媒体库（POST {emby_url}/Library/Refresh）。

    借 QMS 里配置的 Emby 地址/ApiKey（PanKeeper 不重复存一份），且尊重 QMS 的
    「STRM 同步完成后刷新媒体库」开关——定向同步的临时任务跳过了这步，由 PanKeeper 补。"""
    cfg = get_emby_config()
    if not cfg:
        return False, "拿不到 QMS 的 Emby 配置"
    url = (cfg.get("emby_url") or "").rstrip("/")
    key = cfg.get("emby_api_key") or ""
    if not url or not key:
        return False, "QMS 未配置 Emby 地址/ApiKey"
    if int(cfg.get("enable_refresh_library") or 0) != 1:
        return False, "QMS 未开启「同步完成后刷新媒体库」"
    try:
        resp = httpx.post(f"{url}/Library/Refresh", params={"api_key": key}, timeout=15)
        if resp.status_code in (200, 204):
            return True, ""
        return False, f"Emby 返回 HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        return False, f"Emby 连接失败：{e}"


_STRM_PAIR_CACHE: dict[int, tuple[float, int | None]] = {}
_STRM_PAIR_TTL = 300.0  # 配对结果缓存 5 分钟：QMS 侧路径配置不常变，别每次转存都打接口


def strm_id_for_qms(qms_id: int) -> int | None:
    """QMS 刮削目录 → **自动配对**的 STRM 同步路径 id（2026-10-04 用户定稿：
    STRM 跟随 QMS，转存配置里不再单独选）。

    配对规则：刮削路径的整理目标根（dest_path，如 /baidu/0.影视/电影）＝某个
    STRM 同步路径的 remote_path——QMS 整理完的文件落在哪，就同步哪。拿不到返回
    None（调用方按"没配 STRM"处理）。结果缓存 5 分钟。"""
    import time as _time

    qid = int(qms_id)
    hit = _STRM_PAIR_CACHE.get(qid)
    if hit and _time.time() - hit[0] < _STRM_PAIR_TTL:
        return hit[1]
    sp = next((x for x in scrape_pathes() or [] if x.get("id") == qid), None)
    dest = (sp.get("dest_path") or "").strip().strip("/") if sp else ""
    sid: int | None = None
    if dest:
        for p in sync_pathes() or []:
            if (p.get("remote_path") or "").strip().strip("/") == dest:
                sid = p.get("id")
                break
    _STRM_PAIR_CACHE[qid] = (_time.time(), sid)
    return sid


def health() -> dict:
    """QMS 引擎状态（设置页胶囊用，语义同 PanSou 的 /search/health）。

    用「已保存配置」测——反映的是当前联动链路的真实状态，而非输入框草稿。
    """
    cfg = get_group("settings")["qms"]
    if not (cfg.get("url") or "").strip():
        return {"ok": False, "message": "未配置"}
    ok, msg = test_connection()
    return {"ok": ok, "message": msg if not ok else "在线"}


def test_connection(url: str | None = None, apikey: str | None = None) -> tuple[bool, str]:
    """GET /api/user/info 连接测试（网盘连接页/设置页的「测试」按钮）。
    url/apikey 传入时测该值（输入框里正在编辑的值），否则测已保存的。不要求「启用联动」为开。"""
    c = _client(require_enabled=False, url=url, apikey=apikey)
    if c is None:
        return False, "请先填写 QMS 地址"
    client, url = c
    try:
        resp = client.get("/api/user/info")
        if resp.status_code == 200 and resp.json().get("code") in (0, 200):
            return True, "QMS 连接正常"
        return False, f"HTTP {resp.status_code}（检查地址或 API Key）"
    except (httpx.HTTPError, ValueError) as e:
        return False, f"连接失败：{e}"
    finally:
        client.close()
