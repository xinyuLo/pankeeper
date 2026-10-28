"""T-017 · 去重跳过拆三路（台账 / MD5 / 同名）。

三条验收线：台账命中返回名单（名字+MD5）；skip = 台账 + MD5 + 同名 相加自洽（含
全命中短路分支）；非 32 位 hex 的脏 MD5 解析时丢弃只留名字去重。只做管道层断言，
不打真实网盘。
"""
from __future__ import annotations

from app.adapters.base import ShareFile, TaskSpec, TransferResult, clean_md5
from app.db import SessionLocal
from app.models import TransferLedger
from app.transfer.core import transfer_share
from app.transfer.ledger import ledger_prefilter

MD5 = "a" * 32
TASK = 4242  # 台账 task_id 用独立值，不与其它用例串账


class _FakeAdapter:
    """save_files 原样回填预设结果（模拟适配器去重后的产物）；被打即记录。"""

    def __init__(self, result: TransferResult):
        self._result = result
        self.saved: list | None = None

    def save_files(self, files, spec, on_progress, on_log):
        self.saved = list(files)
        return self._result


def _seed_ledger(task_id: int, name: str, md5: str, save_dir: str = "/t017") -> None:
    with SessionLocal() as s:
        s.add(
            TransferLedger(
                drive="baidu", acc_id=0, task_id=task_id, share_url="https://pan.baidu.com/s/t017",
                name=name, md5=md5, save_dir=save_dir, transferred_at="2026-10-27 00:00:00",
            )
        )
        s.commit()


def _drop_ledger(task_id: int) -> None:
    with SessionLocal() as s:
        s.query(TransferLedger).filter(TransferLedger.task_id == task_id).delete()
        s.commit()


def _spec() -> TaskSpec:
    return TaskSpec(share_url="https://pan.baidu.com/s/t017", save_dir="/t017")


def _run(adapter, files: list):
    return transfer_share(
        adapter, _spec(), files, drive="baidu", acc_id=None, task_id=TASK,
        on_progress=lambda p: None, on_log=lambda txt: None, log_step=lambda txt: None,
    )


# ---------- 验收 1：台账命中返回名单 ----------

def test_ledger_prefilter_returns_hit_list():
    _seed_ledger(TASK, "E18.mkv", MD5)
    try:
        files = [
            ShareFile(fid="1", name="E18.mkv", path="/E18.mkv", size=1, md5=MD5),
            ShareFile(fid="2", name="E19.mkv", path="/E19.mkv", size=1, md5="b" * 32),
        ]
        kept, hits = ledger_prefilter("baidu", TASK, "/t017", files)
        assert [f.name for f in kept] == ["E19.mkv"]
        assert hits == [{"name": "E18.mkv", "md5": MD5}]
    finally:
        _drop_ledger(TASK)


# ---------- 验收 2：skip = 台账 + MD5 + 同名，相加自洽 ----------

def test_transfer_share_stats_add_up_partial_hit():
    """部分命中：台账拦 1 项（不进适配器），适配器拦 MD5 2 + 同名 1 —— 四个账对得上。"""
    _seed_ledger(TASK, "E18.mkv", MD5)
    try:
        r = TransferResult(
            add=1, skip=3, skip_md5=2,
            md5_skipped=[{"name": "E1.mkv", "md5": "b" * 32}, {"name": "E2.mkv", "md5": "c" * 32}],
            name_skipped=[{"name": "E3.mkv", "md5_diff": False}],
            transferred=[{"name": "NEW.mkv", "fid": "9"}],
        )
        adapter = _FakeAdapter(r)
        files = [
            ShareFile(fid="1", name="E18.mkv", path="/E18.mkv", size=1, md5=MD5),  # 台账拦（名字+MD5 双中）
            # E1/E2 的 MD5 与台账种子（MD5='a'*32）不同——只走适配器的 MD5 闸，不能被台账提前拦下
            ShareFile(fid="2", name="E1.mkv", path="/E1.mkv", size=1, md5="b" * 32),
            ShareFile(fid="3", name="E2.mkv", path="/E2.mkv", size=1, md5="c" * 32),
            ShareFile(fid="4", name="E3.mkv", path="/E3.mkv", size=1),
            ShareFile(fid="5", name="NEW.mkv", path="/NEW.mkv", size=1),
        ]
        result, all_files = _run(adapter, files)
        assert result.skip_ledger == 1 and result.ledger_skipped == [{"name": "E18.mkv", "md5": MD5}]
        assert result.skip == result.skip_ledger + result.skip_md5 + len(result.name_skipped)
        assert all(f.name != "E18.mkv" for f in (adapter.saved or []))  # 台账命中项没进适配器
        assert len(all_files) == 5  # 详情快照仍拿台账过滤前的全量
    finally:
        _drop_ledger(TASK)


def test_transfer_share_full_hit_short_circuit_carries_list():
    """全命中短路：适配器不被调用，skip 全记台账，名单照样带出（派单目标 2）。"""
    _seed_ledger(TASK, "E18.mkv", MD5)
    try:
        adapter = _FakeAdapter(TransferResult())
        result, all_files = _run(adapter, [ShareFile(fid="1", name="E18.mkv", path="/E18.mkv", size=1, md5=MD5)])
        assert adapter.saved is None  # 短路：适配器没被调
        assert result.skip == result.skip_ledger == 1
        assert result.ledger_skipped == [{"name": "E18.mkv", "md5": MD5}]
        assert result.skip == result.skip_ledger + result.skip_md5 + len(result.name_skipped)
        assert len(all_files) == 1
    finally:
        _drop_ledger(TASK)


# ---------- 验收：非法 MD5 丢弃 ----------

def test_non_hex_md5_kept():
    """2026-10-28 回退：非 hex 的 md5 原样保留（百度会返回此类值），只做 strip/lower。
    早前按 32-hex 丢弃 → 去重基线 MD5 变 0 → run 17/18 全量重复转存。"""
    assert clean_md5("8f635f1f3u7286a57f1e8146dabf732f") == "8f635f1f3u7286a57f1e8146dabf732f"
    assert clean_md5("A" * 32) == "a" * 32
    assert clean_md5("") == "" and clean_md5(None) == ""
    row = {"fs_id": "1", "server_filename": "E18.mkv", "size": 1, "isdir": 0, "path": "/E18.mkv",
           "md5": "8f635f1f3u7286a57f1e8146dabf732f"}
    from app.adapters.baidu import BaiduClient

    f = BaiduClient._share_row_to_file(row, "")
    assert f.md5 == "8f635f1f3u7286a57f1e8146dabf732f" and f.name == "E18.mkv"


# ---------- T-017-F2：勾选过滤差额 / md5_diff 真比对 ----------


def _bd_file(path: str, name: str, md5: str) -> dict:
    return {"server_filename": name, "path": path, "isdir": 0, "size": 10, "md5": md5}


def _bare_baidu(table: dict):
    """BaiduClient（跳过 __init__），list_dir 按 table 应答，HTTP 层哨兵（同 test_dedup_baseline 手法）。"""
    from app.adapters.baidu import BaiduClient as BC

    def _no_http(*_a, **_k):
        raise AssertionError("测试禁止真实网盘请求")

    cli = BC.__new__(BC)

    def fake_list_dir(d):
        return table.get(d) or []

    cli.list_dir = fake_list_dir
    cli._get = _no_http
    cli._share_get = _no_http
    cli._share_post = _no_http
    cli._share_ctx = {"shareid": "1", "uk": "2", "bdstoken": "", "referer": "https://pan.baidu.com/s/x"}
    return cli


def test_baidu_name_hit_no_longer_skips():
    """2026-10-28 老大定：名字不作命中依据（QMS 整理会改文件名）。
    基线里同名但 MD5 不同 / 基线行无 MD5 的，一律按新增转存。"""
    table = {
        "/库": [
            _bd_file("/库/不同.mkv", "不同.mkv", "bb" * 16),  # 同名但 MD5 不同
            _bd_file("/库/无md5.mkv", "无md5.mkv", ""),  # 同名但基线无 MD5
        ],
        "/t017": [],
    }
    cli = _bare_baidu(table)
    spec = TaskSpec(share_url="https://pan.baidu.com/s/t017", save_dir="/t017", compare_path="/库")
    files = [
        ShareFile(fid="1", name="不同.mkv", path="不同.mkv", size=10, md5="aa" * 16),
        ShareFile(fid="2", name="无md5.mkv", path="无md5.mkv", size=10, md5="cc" * 16),
    ]
    cli._ensure_save_dir = lambda *a, **k: None
    cli._ensure_dirs = lambda *a, **k: None

    def fake_transfer(ctx, group, target, spec_, result, on_log):
        for f in group:
            result.transferred.append({"name": f.name, "fid": f.fid})
        result.add += len(group)

    cli._transfer_group = fake_transfer

    result = cli.save_files(files, spec, lambda p: None, lambda txt: None)

    assert result.skip == 0 and result.add == 2
    assert sorted(e["name"] for e in result.transferred) == ["不同.mkv", "无md5.mkv"]
    assert result.name_skipped == []


def test_baidu_only_paths_gap_out_of_three_way():
    """F2 验收：勾选过滤计入 skip 但不入三路 → 差额 = 未勾选数（详情页 footnote 的数据基础）。"""
    table = {"/库": [_bd_file("/库/EP01.mkv", "EP01.mkv", "aa" * 16)], "/t017": []}
    cli = _bare_baidu(table)
    spec = TaskSpec(
        share_url="https://pan.baidu.com/s/t017", save_dir="/t017", compare_path="/库",
        only_paths={"EP01.mkv"},
    )
    files = [
        ShareFile(fid="1", name="EP01.mkv", path="EP01.mkv", size=10, md5="aa" * 16),
        ShareFile(fid="2", name="EP02.mkv", path="EP02.mkv", size=10, md5="bb" * 16),
        ShareFile(fid="3", name="EP03.mkv", path="EP03.mkv", size=10, md5="cc" * 16),
    ]

    result = cli.save_files(files, spec, lambda p: None, lambda txt: None)

    # EP01 MD5 命中跳过；EP02/EP03 未勾选 → 只进 skip，不进三路任何一卡
    assert result.skip == 3 and result.skip_md5 == 1
    assert result.skip_ledger == 0 and len(result.name_skipped) == 0
    gap = result.skip - result.skip_ledger - result.skip_md5 - len(result.name_skipped)
    assert gap == 2
