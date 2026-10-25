"""缓存到期自动预热的到期判定（T-009 重写）：按账号到期 / 入队不记账 / 成功才推进 /
失败短间隔重试 / force 真刷新。"""
from __future__ import annotations

import os
import sys
import tempfile
import time as _time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pk-test-")
os.environ.setdefault("PK_DATA", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.cache_warm as cw  # noqa: E402

HOUR = 3600


def _cfg(master=True, auto=True, ttl=30, unit="小时"):
    return {"master": master, "auto": auto, "ttl": ttl, "ttlUnit": unit}


def _env(monkeypatch, cfg: dict, last) -> dict:
    """接管配置组与持久化，返回捕获到的 save_group 写入。

    last 支持两种形态：dict（按账号）/ int（老结构全局值）。"""
    saved: dict = {}
    state = {
        "last": dict(last) if isinstance(last, dict) else int(last or 0),
        "errors": {},
        "retry": {},
        "fails": {},
    }

    def fake_get(group: str):
        if group == "cache_cfg":
            return dict(cfg)
        if group == cw.WARM_GROUP:
            return {
                "last": state["last"],
                "errors": dict(state["errors"]),
                "retry": dict(state["retry"]),
                "fails": dict(state["fails"]),
            }
        return {}

    def fake_save(group: str, value: dict) -> None:
        if group == cw.WARM_GROUP:
            state["last"] = dict(value.get("last") or {})
            state["errors"] = dict(value.get("errors") or {})
            state["retry"] = dict(value.get("retry") or {})
            state["fails"] = dict(value.get("fails") or {})
            saved.update(_deep(state))

    def _deep(st: dict) -> dict:
        return {
            "last": dict(st["last"]) if isinstance(st["last"], dict) else st["last"],
            "errors": dict(st["errors"]),
            "retry": dict(st["retry"]),
            "fails": dict(st["fails"]),
        }

    monkeypatch.setattr(cw, "get_group", fake_get)
    monkeypatch.setattr(cw, "save_group", fake_save)
    monkeypatch.setattr(cw, "_acc_latest_cached_at", lambda t, a: 0)
    return saved


def test_master_off_never_fires(monkeypatch):
    _env(monkeypatch, _cfg(master=False), {"quark/1": 0})
    r = cw.check_and_fire(now=1000000.0, fire=lambda due: len(due), accounts=[("quark", 1, "夸克")])
    assert r["fired"] == 0 and not r["due"]


def test_auto_off_never_fires(monkeypatch):
    _env(monkeypatch, _cfg(auto=False), {"quark/1": 0})
    r = cw.check_and_fire(now=1000000.0, fire=lambda due: len(due), accounts=[("quark", 1, "夸克")])
    assert r["fired"] == 0 and not r["due"]


def test_not_due_within_ttl(monkeypatch):
    now = 1000000.0
    _env(monkeypatch, _cfg(ttl=30), {"quark/1": int(now - 29 * HOUR)})
    r = cw.check_and_fire(now=now, fire=lambda due: len(due), accounts=[("quark", 1, "夸克")])
    assert r["fired"] == 0
    assert 50 <= r["next_in_min"] <= 70  # 差 1 小时到期


def test_due_fires_only_that_account(monkeypatch):
    """验收 3：A 账号到期、B 未到期 → 只预热 A。"""
    now = 1000000.0
    got: list = []
    _env(monkeypatch, _cfg(ttl=30), {"quark/1": int(now - 31 * HOUR), "baidu/2": int(now - 1 * HOUR)})
    r = cw.check_and_fire(
        now=now,
        fire=lambda due: got.append(due) or len(due),
        accounts=[("quark", 1, "夸克"), ("baidu", 2, "百度")],
    )
    assert r["fired"] == 1 and r["due"] == ["quark/1"]
    assert [k for _, _, k in got[0]] == ["quark/1"]  # 入队名单里只有夸克


def test_enqueue_does_not_advance_last_only_success_does(monkeypatch):
    """验收 2：入队不推进 last；终态回调成功才推进（T-009 铁律：入队≠成功）。"""
    now = 1000000.0
    old_last = int(now - 31 * HOUR)
    saved = _env(monkeypatch, _cfg(ttl=30), {"quark/1": old_last})
    cw.check_and_fire(now=now, fire=lambda due: 1, accounts=[("quark", 1, "夸克")])
    # 入队那一刻不记账：要么一次没写，要么 last 原样
    assert saved.get("last", {}).get("quark/1", old_last) == old_last

    cw._on_warm_done("quark/1", True, "")
    assert saved["last"]["quark/1"] > now + HOUR  # 成功回调才推进（真实时间戳）


def test_failure_keeps_last_and_records_reason(monkeypatch):
    """验收 2：预热失败 last 不推进（下次对表自然重试）、失败原因记账。"""
    now = 1000000.0
    old_last = int(now - 31 * HOUR)
    saved = _env(monkeypatch, _cfg(ttl=30), {"quark/1": old_last})
    cw._on_warm_done("quark/1", False, "QMS 接口 406")
    assert saved["last"]["quark/1"] == old_last  # 不推进：≤10 分钟后下次对表仍判到期
    assert saved["errors"]["quark/1"] == "QMS 接口 406"
    # 成功后错误清掉
    cw._on_warm_done("quark/1", True, "")
    assert saved["errors"] == {}


def test_first_seen_account_seeds_full_ttl(monkeypatch):
    """首见账号（无 last 无缓存条目）从现在起算一个完整 TTL，不立即触发。"""
    now = 1000000.0
    saved = _env(monkeypatch, _cfg(ttl=30), {})
    r = cw.check_and_fire(now=now, fire=lambda due: len(due), accounts=[("quark", 1, "夸克")])
    assert r["fired"] == 0 and r["next_in_min"] == int(30 * HOUR / 60)
    assert saved["last"]["quark/1"] == int(now)  # 起算值落库一次，不是每次对表都重新起算


def test_legacy_global_last_still_honored(monkeypatch):
    """老结构（全局一个 int last）：各账号读它当兜底，不丢到期判定。"""
    now = 1000000.0
    _env(monkeypatch, _cfg(ttl=30), int(now - 31 * HOUR))
    r = cw.check_and_fire(now=now, fire=lambda due: len(due), accounts=[("quark", 1, "夸克"), ("baidu", 2, "百度")])
    assert r["due"] == ["quark/1", "baidu/2"]


def test_ttl_unit_minutes(monkeypatch):
    now = 1000000.0
    _env(monkeypatch, _cfg(ttl=45, unit="分钟"), {"quark/1": int(now - 46 * 60)})
    r = cw.check_and_fire(now=now, fire=lambda due: len(due), accounts=[("quark", 1, "夸克")])
    assert r["fired"] == 1


def test_warm_walk_force_param_controls_cache_bypass(monkeypatch):
    """T-009-F1 验收 1+3 / T-009 验收 1：force 成参数——手动预热（默认 False）命中
    新鲜缓存零网盘；到期触发（True）绕缓存真调 loader。"""
    from app.api import cache_api
    from app.services.dircache import dir_cache

    calls = {"n": 0}

    def fake_load(type_, acc_id, parent, path):
        calls["n"] += 1
        return []  # 空目录：走一层即收工

    monkeypatch.setattr(cache_api, "_load_dir_payload", fake_load)
    dir_cache.clear()
    key = ("quark", "main", "0")
    with dir_cache._lock:  # 直接落一条 5 分钟后才过期的"新鲜"条目
        dir_cache._restored = True
        dir_cache._store[key] = ([{"fid": "x", "name": "a", "is_dir": False, "size": 1}], _time.time() + 300, _time.time())

    job1 = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}
    cache_api._warm_walk("quark", None, job1)  # 手动「全部预热」：不带 force
    assert calls["n"] == 0 and job1["status"] == "done"  # 命中缓存，零网盘请求

    job2 = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}
    cache_api._warm_walk("quark", None, job2, force=True)  # 到期自动预热：force 真拉
    assert calls["n"] >= 1
    assert job2["status"] == "done"
    dir_cache.clear()


def test_legacy_last_fanout_keeps_all_accounts(monkeypatch):
    """T-009-F1 验收 1：legacy 全局 last 在首个成功回写时摊给所有账号——
    另一个到期账号不丢起算值（不被 seed 成 now 白等一个 TTL）。"""
    legacy = int(_time.time() - 31 * HOUR)
    saved = _env(monkeypatch, _cfg(ttl=30), legacy)
    monkeypatch.setattr(cw, "_accounts", lambda: [("quark", 1, "夸克"), ("baidu", 2, "百度")])

    cw._on_warm_done("quark/1", True, "")  # 夸克先成功：回写会带 fanout

    assert saved["last"]["baidu/2"] == legacy  # 百度的起算值还在（= legacy），没被抹
    assert saved["last"]["quark/1"] > legacy  # 夸克按成功时刻推进


def test_failure_backoff_blocks_and_doubles(monkeypatch):
    """T-009-F1 验收 2：失败写 retry_at（10 分钟起）——退避期内即使已到期也不触发；
    连败翻倍；成功清空。"""
    real_now = _time.time()  # _on_warm_done 用真实时钟
    old_last = int(real_now - 31 * HOUR)
    saved = _env(monkeypatch, _cfg(ttl=30), {"quark/1": old_last})

    cw._on_warm_done("quark/1", False, "风控")
    r1 = saved["retry"]["quark/1"]
    assert 590 <= r1 - real_now <= 615  # 首败退避 ~10 分钟

    # 退避期内（last 早已过期）：不触发（fire 装哨兵，被调即炸）
    r = cw.check_and_fire(
        now=real_now + 300,
        fire=lambda due: (_ for _ in ()).throw(AssertionError("退避期内不应触发")),
        accounts=[("quark", 1, "夸克")],
    )
    assert r["fired"] == 0

    cw._on_warm_done("quark/1", False, "风控2")  # 再败：翻倍 ~20 分钟
    r2 = saved["retry"]["quark/1"]
    assert 1150 <= r2 - real_now <= 1230
    assert saved["fails"]["quark/1"] == 2

    cw._on_warm_done("quark/1", True, "")  # 成功：清退避与计数
    assert saved["retry"] == {} and saved["fails"] == {}


def test_warm_walk_paces_requests_through_shared_gate(monkeypatch):
    """T-009-F2 验收：force 走查的每个目录请求前都过共享限速门——相邻请求间隔
    满足该网盘最小间隔（可注入 gate 记时间）。"""
    from app.adapters.rate_gate import RateGate
    from app.api import cache_api
    from app.services.dircache import dir_cache

    class _RecGate(RateGate):
        def __init__(self):
            super().__init__("quark", min_interval=0.05, cooldown=5)
            self.stamps: list[float] = []

        def wait(self):
            waited = super().wait()
            self.stamps.append(_time.perf_counter())  # 过完门即将发请求的时刻（高精度钟，别用 time.time——Windows 量化误差 ~15ms）
            return waited

    dirs = {"0": [{"fid": f"d{i}", "name": f"D{i}", "is_dir": True, "size": 0} for i in range(3)]}

    def fake_load(type_, acc_id, parent, path):
        return dirs.get(parent, [])

    monkeypatch.setattr(cache_api, "_load_dir_payload", fake_load)
    dir_cache.clear()
    g = _RecGate()
    job = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}

    cache_api._warm_walk("quark", None, job, force=True, gate=g)

    assert job["status"] == "done" and job["done"] == 4  # 根 + 3 个子目录
    assert len(g.stamps) == 4  # 每一步真打网盘前都过门
    gaps = [b - a for a, b in zip(g.stamps, g.stamps[1:])]
    # Windows 调度允许偶发少醒一个时钟节拍（~15ms），断言按「无连发 + 总节奏」表述：
    # 没有门时间距是 ~1ms 量级，min≥0.02 + sum≥3×0.045 足以证明限速生效且依然锋利
    assert min(gaps) >= 0.02
    assert sum(gaps) >= 3 * 0.045
    dir_cache.clear()


def test_acc_latest_cached_at_isolated_per_account(monkeypatch):
    """T-009-F3 验收 1：非默认账号不吃默认账号（main 键）的缓存——起算值互不影响。"""
    from app.db import SessionLocal
    from app.models import DirTreeCacheRow

    def fake_get(group: str):
        if group == "default_accounts":
            return {"quark": 1}
        if group == "cache_cfg":
            return _cfg()
        return {}

    monkeypatch.setattr(cw, "get_group", fake_get)
    now = int(_time.time())
    with SessionLocal() as s:
        # 先清掉同盘残留行（前面 warm_walk 用例的持久化缓存），保证查询只看到本例的两行
        s.query(DirTreeCacheRow).filter(DirTreeCacheRow.type == "quark").delete()
        s.add(DirTreeCacheRow(type="quark", acc="main", cid="p-t009f3-main", items_json="[]", expires_at=now + 3600, cached_at=now - 5000))
        s.add(DirTreeCacheRow(type="quark", acc="2", cid="p-t009f3-2", items_json="[]", expires_at=now + 3600, cached_at=now - 100))
        s.commit()
    try:
        assert cw._acc_latest_cached_at("quark", 1) == now - 5000  # 默认账号（id=1）认 main 键
        assert cw._acc_latest_cached_at("quark", 2) == now - 100  # 非默认只认自己的键
    finally:
        with SessionLocal() as s:
            s.query(DirTreeCacheRow).filter(DirTreeCacheRow.type == "quark").delete()
            s.commit()


# ---------- T-010 · 预热起点改成默认根目录（root_cfg） ----------


def _warm_env(monkeypatch, tree: dict, root_cfg: dict) -> list[str]:
    """接管 _load_dir_payload（按 parent 应答，记录调用）与 root_cfg 配置组。"""
    from app.api import cache_api

    calls: list[str] = []

    def fake_load(type_, acc_id, parent, path):
        calls.append(parent)
        return tree.get(parent, [])

    monkeypatch.setattr(cache_api, "_load_dir_payload", fake_load)

    def fake_get(group: str):
        if group == "root_cfg":
            return dict(root_cfg)
        return {}

    monkeypatch.setattr(cache_api, "get_group", fake_get)
    return calls


def test_warm_walk_starts_from_root_cfg(monkeypatch):
    """T-010 验收 1：配了 root_cfg 时，BFS 第一个列目录目标就是该目录（真根只用于
    路径→fid 解析，不会被当作扫描起点展开无关目录）。"""
    from app.api import cache_api
    from app.services.dircache import dir_cache

    tree = {
        "0": [
            {"fid": "m", "name": "1.影视", "is_dir": True, "size": 0},
            {"fid": "junk", "name": "临时存储", "is_dir": True, "size": 0},
        ],
        "m": [{"fid": "movie", "name": "电影", "is_dir": True, "size": 0}],
        "movie": [],
        "junk": [{"fid": "j1", "name": "无关", "is_dir": True, "size": 0}],
    }
    calls = _warm_env(monkeypatch, tree, {"quark": "/1.影视"})
    dir_cache.clear()
    job = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}

    cache_api._warm_walk("quark", None, job)

    # 解析链只列真根找「1.影视」；BFS 从 fid=m 起步 → 电影 → 完。无关目录 junk 全程不列。
    assert calls == ["0", "m", "movie"]
    assert job["status"] == "done" and job["done"] == 2  # 只计 m + 电影（解析那次不计额度）
    dir_cache.clear()


def test_warm_walk_baidu_root_cfg_path_used_directly(monkeypatch):
    """百度：路径即目录标识——起点直接就是配置路径，零解析请求。"""
    from app.api import cache_api
    from app.services.dircache import dir_cache

    calls = _warm_env(monkeypatch, {"/A罗/0.影视": []}, {"baidu": "/A罗/0.影视"})
    dir_cache.clear()
    job = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}

    cache_api._warm_walk("baidu", None, job)

    assert calls == ["/A罗/0.影视"]
    assert job["status"] == "done"
    dir_cache.clear()


def test_warm_walk_without_root_cfg_uses_real_root(monkeypatch):
    """T-010 验收 2：root_cfg 没配 → 从真根开始（原行为不变）。"""
    from app.api import cache_api
    from app.services.dircache import dir_cache

    calls = _warm_env(monkeypatch, {"0": [{"fid": "d0", "name": "A", "is_dir": True, "size": 0}], "d0": []}, {})
    dir_cache.clear()
    job = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}

    cache_api._warm_walk("quark", None, job)

    assert calls == ["0", "d0"]
    assert job["status"] == "done" and job["done"] == 2
    dir_cache.clear()


def test_warm_walk_root_cfg_resolve_failure_falls_back_with_log(monkeypatch, capsys):
    """T-010 验收 3：配了但解析失败（目录不存在）→ 回落真根继续扫 + 日志说明。"""
    from app.api import cache_api
    from app.services.dircache import dir_cache

    tree = {"0": [{"fid": "m", "name": "1.影视", "is_dir": True, "size": 0}], "m": []}
    calls = _warm_env(monkeypatch, tree, {"quark": "/不存在的路"})
    dir_cache.clear()
    job = {"status": "queued", "done": 0, "total": 1, "message": "", "acc_id": None}

    cache_api._warm_walk("quark", None, job)

    out = capsys.readouterr().out
    assert "解析失败" in out and "回落真根" in out  # 日志写明原因
    assert job["status"] == "done"  # 回落真根照常扫完（真根层已缓存命中，BFS 继续列 m）
    assert "m" in calls
    dir_cache.clear()
