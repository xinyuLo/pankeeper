"""T-011 · 转存管道共享内核 core.py 的行为锚点（管道抽出后两条链路必须不变的东西）。

全部 mock（适配器/后台链路/推送），零真实网盘请求；库用 conftest 的临时库。
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from app.adapters.base import AdapterError, CredentialExpired, ShareBanned, ShareFile, TaskSpec, TransferResult
from app.db import SessionLocal
from app.models import Record, TransferLedger
from app.services import media_push, notify, run_watch
from app.transfer import core
from app.transfer.ledger import ledger_write

MARK = "https://pan.quark.cn/s/t011core"


@pytest.fixture(autouse=True)
def _clean():
    with SessionLocal() as s:
        s.query(TransferLedger).delete()
        s.commit()
    yield
    with SessionLocal() as s:
        s.query(TransferLedger).delete()
        s.query(Record).filter(Record.share_url == MARK).delete()
        s.commit()


def _eng():
    lock = threading.Lock()
    return SimpleNamespace(_lock=lock, _cond=threading.Condition(lock), state={})


def _t() -> dict:
    return {
        "name": "T011剧.S01", "type": "quark", "path": "/d", "shareUrl": MARK,
        "shareCode": "", "logs": [], "files": 1,
    }


def _result() -> TransferResult:
    r = TransferResult()
    r.add = 1
    r.transferred = [{"name": "EP.mkv", "fid": "1"}]
    return r


# ---------- transfer_share ----------


def test_transfer_share_full_ledger_hit_skips_adapter():
    """验收 3-①：台账全命中 → 零网盘调用（适配器被打即炸）+ result.skip == 命中数。"""
    files = [ShareFile(fid="1", name="EP.mkv", path="/EP.mkv", size=1, md5="cc" * 16)]
    r0 = TransferResult()
    r0.transferred = [{"name": "EP.mkv", "fid": "1"}]
    ledger_write("quark", None, 0, MARK, "/d", files, r0)

    class _SentinelAdapter:
        def save_files(self, *a, **k):
            raise AssertionError("台账应全命中：save_files 不应被调用（不应请求网盘）")

    spec = TaskSpec(share_url=MARK, save_dir="/d")
    result, all_files = core.transfer_share(
        _SentinelAdapter(), spec, files,
        drive="quark", acc_id=None, task_id=0,
        on_progress=lambda p: None, on_log=lambda txt: None, log_step=lambda txt: None,
    )
    assert result.skip == 1 and result.add == 0
    assert len(all_files) == 1  # 过滤前完整清单带回（详情快照用）


# ---------- finish：hist_sink / notify_kind / enabled_gate ----------


@pytest.fixture()
def chain_mocks(monkeypatch):
    """接管后台链路与推送，捕获调用。"""
    calls = {"watch_qms": [], "strm": [], "touch": 0, "spawn": [], "push": [], "resolver": [], "interim": []}

    def fake_watch_qms(run_id, task, names, **kw):
        calls["watch_qms"].append((run_id, kw))

    def fake_strm(run_id, qms_id, strm_id, delay=0, **kw):
        calls["strm"].append((run_id, qms_id, strm_id))

    monkeypatch.setattr(run_watch, "watch_qms", fake_watch_qms)
    monkeypatch.setattr(run_watch, "trigger_strm_after_scrape", fake_strm)
    monkeypatch.setattr(run_watch, "touch_records", lambda: calls.__setitem__("touch", calls["touch"] + 1))
    monkeypatch.setattr(run_watch, "write_qms_interim", lambda rid, st, table=None: calls["interim"].append((rid, st, table)))
    monkeypatch.setattr(media_push, "watch_and_spawn", lambda ctx: calls["spawn"].append(ctx))
    monkeypatch.setattr(notify, "push", lambda title, body, **kw: calls["push"].append(kw.get("kind")))
    return calls


def test_finish_record_sink_uses_record_id(chain_mocks):
    """验收 3-②：hist_sink="record" → 后台 watch_qms 拿到的 id 是 Record.id。"""
    t = _t()
    t["_media"] = {"qms_id": 3, "strm_id": None}
    t["_qms_baseline"] = {}

    core.finish(
        _eng(), t, "done",
        qms_snap={"st": "已触发", "cls": "t-run"}, result=_result(),
        record_source="search", hist_sink="record", notify_kind="search_fail",
    )

    with SessionLocal() as s:
        rid = s.query(Record).filter(Record.share_url == MARK).order_by(Record.id.desc()).first().id
    assert len(chain_mocks["watch_qms"]) == 1
    assert chain_mocks["watch_qms"][0][0] == rid           # 挂的是 Record.id
    kw = chain_mocks["watch_qms"][0][1]
    assert kw.get("table") is Record and callable(kw.get("on_state"))  # record 链路：table=Record + on_state 回填（改前原文如此）
    kw["on_state"]("整理中")
    assert chain_mocks["interim"] == [(rid, "整理中", Record)]
    assert chain_mocks["spawn"][0]["run_id"] == rid and chain_mocks["spawn"][0]["strm_table"] == "Record"
    assert chain_mocks["touch"] == 1
    assert chain_mocks["push"] == []  # done 不推失败


def test_finish_pa_sink_uses_runhistory_id(chain_mocks):
    """验收 3-② + F1：hist_sink="pa" → 后台链路挂 run_history_writer 返回的 RunHistory.id，
    STRM 目标由 resolver 现场解析；watch_qms 带 on_state 回填 RunHistory（默认表，
    不带 table）——F1 修的是 T-011 曾把这个 on_state 丢了。"""
    t = _t()
    t["_qms_baseline"] = {}

    def writer(t_, status, result, q, s):
        return 777

    def resolver(t_):
        chain_mocks["resolver"].append(t_["path"])
        return {"qms_id": 2, "strm_id": 5}

    core.finish(
        _eng(), t, "done",
        qms_snap={"st": "已触发", "cls": "t-run"}, result=_result(),
        record_source="auto", hist_sink="pa", notify_kind="auto_fail",
        run_history_writer=writer, strm_link_resolver=resolver,
    )

    assert chain_mocks["watch_qms"][0][0] == 777                    # 挂的是 RunHistory.id
    kw = chain_mocks["watch_qms"][0][1]
    assert "table" not in kw and callable(kw.get("on_state"))       # pa 链路：默认表（RunHistory）+ on_state 回填
    kw["on_state"]("正在刮削")
    assert chain_mocks["interim"] == [(777, "正在刮削", None)]       # 中间态写回的就是链路挂的那个 run_id
    assert chain_mocks["strm"] == [(777, 2, 5)]                     # STRM 目标来自 resolver 现场解析
    assert chain_mocks["spawn"][0]["run_id"] == 777 and "strm_table" not in chain_mocks["spawn"][0]
    assert chain_mocks["touch"] == 1
    assert chain_mocks["resolver"] == ["/d"]


def test_finish_pa_writer_none_skips_history_and_chain(chain_mocks):
    """hist_sink="pa" 但无 paTaskId/PaTask 行不在（writer 返回 None）→ RunHistory 与
    后台链路一并跳过（含 touch_records），与既有行为一致。"""
    core.finish(
        _eng(), _t(), "done",
        qms_snap={"st": "已触发", "cls": "t-run"}, result=_result(),
        record_source="auto", hist_sink="pa", notify_kind="auto_fail",
        run_history_writer=lambda *a: None, strm_link_resolver=lambda t: None,
    )
    assert chain_mocks["watch_qms"] == [] and chain_mocks["touch"] == 0


def test_finish_notify_kind_and_enabled_gate(chain_mocks):
    """验收 3-③：notify_kind 分别落 auto_fail / search_fail；enabled_gate=False 不推送。"""
    core.finish(_eng(), _t(), "fail", message="x", record_source="auto", hist_sink="pa",
                notify_kind="auto_fail", run_history_writer=lambda *a: None)
    assert chain_mocks["push"] == ["auto_fail"]

    chain_mocks["push"].clear()
    core.finish(_eng(), _t(), "fail", message="x", record_source="search", hist_sink="record",
                notify_kind="search_fail")
    assert chain_mocks["push"] == ["search_fail"]

    chain_mocks["push"].clear()
    core.finish(_eng(), _t(), "fail", message="x", record_source="auto", hist_sink="pa",
                notify_kind="auto_fail", enabled_gate=False, run_history_writer=lambda *a: None)
    assert chain_mocks["push"] == []  # enabled_gate=False（任务停用）不推送


# ---------- push_log ----------


def test_push_log_drops_oldest_beyond_max():
    """验收 3-④：超过 MAX_LOGS=40 丢最旧的。"""
    t = {"logs": []}
    for i in range(45):
        core.push_log(t, "INFO", f"#{i}")
    assert len(t["logs"]) == core.MAX_LOGS == 40
    assert t["logs"][0]["txt"] == "#5" and t["logs"][-1]["txt"] == "#44"
    assert set(t["logs"][0].keys()) == {"lv", "txt"}  # 结构保持 {lv, txt}


# ---------- T-011-F2：auto 收尾点的 enabled_gate（停用任务失败不推送） ----------


def test_run_auto_disabled_task_fail_not_pushed(monkeypatch):
    """F2 验收：走 run_auto 的调用点（非 core 单测）——enabled=False 失败不推送，
    enabled=True 推 auto_fail。"""
    from app.services import notify as notify_mod
    from app.transfer import auto as auto_mod

    pushed: list[str] = []
    monkeypatch.setattr(notify_mod, "push", lambda title, body, **kw: pushed.append(kw.get("kind")))

    def boom(*a, **k):
        raise AdapterError("无适配器")

    monkeypatch.setattr(auto_mod, "make_adapter", boom)

    def _t(enabled):
        return {"logs": [], "name": "T011F2.S01", "type": "quark", "shareUrl": MARK,
                "shareCode": "", "path": "/d", "includeSubdirs": True, "enabled": enabled}

    auto_mod.run_auto(_eng(), _t(False), {})
    assert pushed == []  # 停用任务的失败不推手机
    auto_mod.run_auto(_eng(), _t(True), {})
    assert pushed == ["auto_fail"]


# ---------- T-011-F3：异常路径的失败详情清单不退化 ----------


def _fail_adapter(exc):
    class _A:
        def list_share(self, spec):
            return [ShareFile(fid="1", name="A.mkv", path="/A.mkv", size=1),
                    ShareFile(fid="2", name="B.mkv", path="/B.mkv", size=1)]

        def save_files(self, files, spec, on_progress, on_log):
            raise exc

    return _A()


def _manual_t(**extra):
    t = {"logs": [], "name": "T011F3.S01", "type": "quark", "shareUrl": MARK, "shareCode": "",
         "path": "/d", "includeSubdirs": True, "mediaOff": True}
    t.update(extra)
    return t


@pytest.mark.parametrize("exc", [AdapterError("网络异常"), ShareBanned("分享已取消"), CredentialExpired("Cookie 已失效")])
def test_manual_fail_paths_keep_full_files_snap(monkeypatch, exc):
    """F3 验收 1：save_files 阶段抛错 → 失败记录 files_json 仍是完整清单（非 []），
    逐项「未转存」。"""
    from app.services import notify as notify_mod
    from app.transfer import manual as manual_mod

    monkeypatch.setattr(manual_mod, "make_adapter", lambda *a, **k: _fail_adapter(exc))
    monkeypatch.setattr(notify_mod, "push", lambda *a, **k: None)
    if isinstance(exc, CredentialExpired):
        monkeypatch.setattr(manual_mod, "_mark_account_expired", lambda *a: None)  # 不动共享库的账号种子

    manual_mod.run_manual(_eng(), _manual_t(), {})

    with SessionLocal() as s:
        r = s.query(Record).filter(Record.share_url == MARK).order_by(Record.id.desc()).first()
        rows = json.loads(r.files_json)
    assert len(rows) == 2
    assert {row["st"] for row in rows} == {"未转存"}


def test_manual_done_files_snap_destinations(monkeypatch):
    """F3 验收 2：正常 done 的 files_json 三种去向齐全（已转存/未勾选/已在库跳过）。"""
    from app.transfer import manual as manual_mod

    class _Ok:
        def list_share(self, spec):
            return [ShareFile(fid="1", name="A.mkv", path="/A.mkv", size=1),
                    ShareFile(fid="2", name="B.mkv", path="/B.mkv", size=1),
                    ShareFile(fid="3", name="C.mkv", path="/C.mkv", size=1)]

        def save_files(self, files, spec, on_progress, on_log):
            on_progress(100)
            r = TransferResult()
            r.add = 1
            r.transferred = [{"name": "A.mkv", "fid": "9"}]
            return r

    monkeypatch.setattr(manual_mod, "make_adapter", lambda *a, **k: _Ok())
    t = _manual_t()
    t["filePaths"] = {"/A.mkv", "/C.mkv"}  # 勾了 A、C；B 未勾选；适配器只转成 A（C 落「已在库跳过」）
    manual_mod.run_manual(_eng(), t, {})

    with SessionLocal() as s:
        r = s.query(Record).filter(Record.share_url == MARK).order_by(Record.id.desc()).first()
        rows = {row["name"]: row["st"] for row in json.loads(r.files_json)}
    assert rows == {"A.mkv": "已转存", "B.mkv": "未勾选", "C.mkv": "已在库跳过"}


# ---------- T-015：auto 终态回填同步记录页 Record ----------


def test_finish_pa_links_record_to_runhistory(chain_mocks):
    """验收 1：finish(hist_sink="pa") 落库后 Record.run_history_id == 对应 RunHistory.id。"""
    from app.models import RunHistory as RH

    t = _t()
    t["_qms_baseline"] = {}

    def writer(t_, status, result, q, s):
        with SessionLocal() as s2:
            h = RH(task_id=424242, started="x", status="running")
            s2.add(h)
            s2.commit()
            return h.id

    core.finish(
        _eng(), t, "done",
        qms_snap={"st": "已触发", "cls": "t-run"}, result=_result(),
        record_source="auto", hist_sink="pa", notify_kind="auto_fail",
        run_history_writer=writer, strm_link_resolver=lambda t_: None,
    )
    try:
        with SessionLocal() as s:
            rec = s.query(Record).filter(Record.share_url == MARK).order_by(Record.id.desc()).first()
            hist = s.get(RH, rec.run_history_id)
        assert rec.run_history_id is not None and hist is not None
        assert rec.run_history_id == hist.id
    finally:
        with SessionLocal() as s:
            s.query(RH).filter(RH.task_id == 424242).delete()
            s.commit()


def test_write_terminal_syncs_linked_record():
    """验收 2：_write / _write_strm 后，RunHistory 与关联 Record 的 qms_json / strm_json 完全相等。"""
    import app.services.run_watch as rw
    from app.models import RunHistory as RH

    with SessionLocal() as s:
        h = RH(task_id=424243, started="x", status="running",
               qms_json='{"st":"已触发","cls":"t-run"}', strm_json='{"st":"等待中","cls":"t-wait"}')
        s.add(h)
        s.commit()
        hid = h.id
        rec = Record(n="T015", t="quark", p="/d", st="完成 1/1", cls="t-ok", tm="x",
                     share_url=MARK, run_history_id=hid,
                     qms_json='{"st":"已触发","cls":"t-run"}', strm_json='{"st":"等待中","cls":"t-wait"}')
        s.add(rec)
        s.commit()
        rid = rec.id
    try:
        rw._write(hid, {"st": "成功", "cls": "t-ok"})
        rw._write_strm(hid, {"st": "已触发", "cls": "t-ok"})
        with SessionLocal() as s:
            h2 = s.get(RH, hid)
            r2 = s.get(Record, rid)
        assert r2.qms_json == h2.qms_json == json.dumps({"st": "成功", "cls": "t-ok"}, ensure_ascii=False)
        assert r2.strm_json == h2.strm_json == json.dumps({"st": "已触发", "cls": "t-ok"}, ensure_ascii=False)
    finally:
        with SessionLocal() as s:
            s.query(RH).filter(RH.task_id == 424243).delete()
            s.query(Record).filter(Record.id == rid).delete()
            s.commit()


def test_record_sink_leaves_link_none_and_scopes_writes(chain_mocks):
    """验收 3：manual 链路 Record.run_history_id 为 NULL；_write_strm(table=Record) 只更新该 Record。"""
    import app.services.run_watch as rw

    t = _t()
    t["_media"] = {"qms_id": 3, "strm_id": None}
    core.finish(
        _eng(), t, "done",
        qms_snap={"st": "已触发", "cls": "t-run"}, result=_result(),
        record_source="search", hist_sink="record", notify_kind="search_fail",
    )
    with SessionLocal() as s:
        rec = s.query(Record).filter(Record.share_url == MARK).order_by(Record.id.desc()).first()
        assert rec.run_history_id is None  # manual 不建关联
        rid = rec.id
        other = Record(n="other", t="quark", p="/x", st="x", cls="t-ok", tm="x", share_url=MARK + "2",
                       strm_json='{"st":"等待中","cls":"t-wait"}')
        s.add(other)
        s.commit()
        oid = other.id
    try:
        rw._write_strm(rid, {"st": "新增 3 个", "cls": "t-ok"}, Record)
        with SessionLocal() as s:
            a = s.get(Record, rid)
            b = s.get(Record, oid)
        assert a.strm_json == json.dumps({"st": "新增 3 个", "cls": "t-ok"}, ensure_ascii=False)
        assert b.strm_json == '{"st":"等待中","cls":"t-wait"}'  # 无关行不被波及
    finally:
        with SessionLocal() as s:
            s.query(Record).filter(Record.id == oid).delete()
            s.commit()
