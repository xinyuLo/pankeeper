"""T-001 去重基线递归扫描子目录（百度/夸克/115）单测。

全部 mock 列目录原语（list_dir / _list_dir / _list_own_dir），并给 _get/_req/_post
装哨兵——任何真实网盘请求意图都会让测试炸掉。目录缓存（dir_cache）用真实现
（conftest 的临时库），每例前后清空隔离。

覆盖验收点：
1. base 下只有 Season 1 子目录、文件在子目录内 → 名字/MD5 进基线并被跳过（百度+夸克）；
2. 一层全部命中 → 不产生任何子目录请求（惰性递归）；
3. 子目录数超上限 → 停止递归且不抛异常（日志含「因上限停止递归」）；
4. 子目录命中 dir_cache → 不再打网盘；
5. 夸克扫 compare_path 不建目录（compare_path 缺失时不发任何创建请求）；
6. CredentialExpired 从子目录扫描向上抛，不被吞。
"""
import pytest

from app.adapters.base import AdapterError, CredentialExpired, ShareFile, TaskSpec, TransferResult
from app.adapters.baidu import BaiduClient
from app.adapters.pan115 import Pan115Adapter
from app.adapters.quark import QuarkAdapter
from app.adapters.rate_gate import CircuitOpen
from app.services.dircache import dir_cache

MD5 = "aa" * 16


def _no_http(*_a, **_k):
    raise AssertionError("测试禁止真实网盘请求（HTTP 层被调用）")


@pytest.fixture(autouse=True)
def _clean_dir_cache():
    dir_cache.clear()
    yield
    dir_cache.clear()


def _baidu(table: dict, calls: list) -> BaiduClient:
    """BaiduClient（跳过 __init__）：list_dir 按 table 应答并记录调用。
    table 值为 Exception 实例时抛出（模拟网络抖动/风控/凭据失效）。"""

    cli = BaiduClient.__new__(BaiduClient)

    def fake_list_dir(d):
        calls.append(d)
        v = table.get(d)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise AdapterError(f"目录不存在：{d}")
        return v

    cli.list_dir = fake_list_dir
    cli._get = _no_http
    cli._share_get = _no_http
    cli._share_post = _no_http
    cli._share_ctx = {"shareid": "1", "uk": "2", "bdstoken": "", "referer": "https://pan.baidu.com/s/x"}
    return cli


def _quark(table: dict, calls: list) -> QuarkAdapter:
    """QuarkAdapter（跳过 __init__）：_list_dir 按 fid 应答并记录调用。
    table 值为 Exception 实例时抛出（模拟网络抖动/风控/凭据失效）。"""
    ad = QuarkAdapter.__new__(QuarkAdapter)

    def fake_list_dir(fid):
        calls.append(fid)
        v = table.get(fid)
        if isinstance(v, Exception):
            raise v
        return v or []

    ad._list_dir = fake_list_dir
    ad._req = _no_http  # 建目录/任何真实请求都会炸——顺带守住 compare_path 不建目录
    ad._stoken = "st"
    return ad


def _p115(table: dict, calls: list) -> Pan115Adapter:
    """Pan115Adapter（跳过 __init__）：_list_own_dir 按 cid 应答并记录调用。
    table 值为 Exception 实例时抛出（模拟网络抖动/风控/凭据失效）。"""
    ad = Pan115Adapter.__new__(Pan115Adapter)

    def fake_list(cid):
        calls.append(cid)
        v = table.get(cid)
        if isinstance(v, Exception):
            raise v
        return v or []

    ad._list_own_dir = fake_list
    ad._get = _no_http
    ad._post = _no_http
    return ad


def _bd_dir(path: str, name: str) -> dict:
    return {"server_filename": name, "path": path, "isdir": 1, "size": 0}


def _bd_file(path: str, name: str, md5: str = MD5) -> dict:
    return {"server_filename": name, "path": path, "isdir": 0, "size": 10, "md5": md5}


SEASON1 = "/媒体库/剧名/Season 1"


# ---------- 百度 ----------


def test_baidu_save_files_skips_file_inside_subdir():
    """验收 1（百度）：剧名目录一层只有 Season 1 文件夹，文件在子目录里——
    名字与 MD5 仍进基线，save_files 判重复跳过，不再转存。"""
    table = {
        "/媒体库/剧名": [_bd_dir(SEASON1, "Season 1")],
        SEASON1: [_bd_file(SEASON1 + "/EP01.mkv", "EP01.mkv")],
        "/待整理": [],
    }
    calls: list = []
    cli = _baidu(table, calls)
    logs: list = []
    spec = TaskSpec(share_url="https://pan.baidu.com/s/1abcDEF-_123456789012", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="123", name="EP01.mkv", path="EP01.mkv", size=10, md5=MD5)]

    result = cli.save_files(files, spec, lambda p: None, logs.append)

    assert result.skip == 1 and result.add == 0 and result.skip_md5 == 1
    assert result.md5_skipped == [{"name": "EP01.mkv", "md5": MD5}]
    assert calls.count(SEASON1) == 1  # 子目录确实下钻了一次
    assert any("扫描了 3 个目录（含子目录）" in m for m in logs)  # 剧名 + Season 1 + 待整理


def test_baidu_lazy_no_subdir_request_when_level1_hits():
    """验收 3（百度）：一层就全部命中 → 不产生任何子目录请求。"""
    table = {
        "/媒体库/剧名": [
            _bd_file("/媒体库/剧名/EP01.mkv", "EP01.mkv"),
            _bd_dir(SEASON1, "Season 1"),  # 有子目录也不该被下钻
        ],
        "/待整理": [],
    }
    calls: list = []
    cli = _baidu(table, calls)
    spec = TaskSpec(share_url="https://pan.baidu.com/s/1abcDEF-_123456789012", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="123", name="EP01.mkv", path="EP01.mkv", size=10, md5=MD5)]

    result = cli.save_files(files, spec, lambda p: None, lambda m: None)

    assert result.skip == 1
    assert calls == ["/媒体库/剧名", "/待整理"]  # Season 1 一次都没列


def test_baidu_subdir_scan_stops_at_limit():
    """验收 4（百度）：子目录超过上限（默认 3）→ 停止递归、不抛异常、日志留痕。"""
    table = {"/base": [_bd_dir(f"/base/S{i}", f"S{i}") for i in range(1, 6)]}
    for i in range(1, 6):
        table[f"/base/S{i}"] = []
    calls: list = []
    cli = _baidu(table, calls)
    logs: list = []
    st: dict = {}

    names, _ = cli._dir_baselines("/base", cands=[ShareFile(fid="1", name="X.mkv")], budget=[3], on_log=logs.append, stats=st)

    assert names == set()
    assert st["dirs"] == 4 and st["stopped"] is True  # base + 3 个子目录
    subdir_calls = [c for c in calls if c.startswith("/base/S")]
    assert subdir_calls == ["/base/S1", "/base/S2", "/base/S3"]
    assert any("因上限停止递归" in m for m in logs)


def test_baidu_old_shape_cache_repulls_and_md5_dedup_works():
    """F1 验收 1：前端浏览写入的缓存（无 md5 键）被基线命中 → 判旧格式 force 重拉
    一次 → MD5 仍参与比对并跳过。缓存用「前端形状」构造，不用自造带 md5 的形状。"""
    dir_cache.get_or_load(
        ("baidu", "main", SEASON1),
        lambda: [{"fid": f"{SEASON1}/EP01.mkv", "name": "EP01.mkv", "is_dir": False, "size": 10}],
    )
    table = {
        "/媒体库/剧名": [_bd_dir(SEASON1, "Season 1")],
        SEASON1: [_bd_file(SEASON1 + "/EP01.mkv", "EP01.mkv")],  # 重拉拿到的带 md5 原始行
        "/待整理": [],
    }
    calls: list = []
    cli = _baidu(table, calls)
    logs: list = []
    spec = TaskSpec(share_url="https://pan.baidu.com/s/1abcDEF-_123456789012", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="123", name="EP01.mkv", path="EP01.mkv", size=10, md5=MD5)]

    result = cli.save_files(files, spec, lambda p: None, logs.append)

    assert result.skip == 1 and result.skip_md5 == 1  # 重拉补齐的 MD5 参与了比对
    assert calls.count(SEASON1) == 1  # 正是旧格式触发的那一次 force 重拉
    assert any("旧格式" in m for m in logs)


def test_baidu_new_shape_cache_no_cloud():
    """F1 验收 2：新形状缓存（带 md5，由基线自己写入）→ 不回源，子目录零网盘调用。"""
    table = {
        "/媒体库/剧名": [_bd_dir(SEASON1, "Season 1")],
        SEASON1: [_bd_file(SEASON1 + "/EP01.mkv", "EP01.mkv")],
        "/待整理": [],
    }
    calls: list = []
    cli = _baidu(table, calls)
    spec = TaskSpec(share_url="https://pan.baidu.com/s/1abcDEF-_123456789012", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="123", name="EP01.mkv", path="EP01.mkv", size=10, md5=MD5)]

    cli.save_files(files, spec, lambda p: None, lambda m: None)  # 第一遍：miss → 写入新形状缓存
    calls.clear()
    result = cli.save_files(files, spec, lambda p: None, lambda m: None)  # 第二遍：命中缓存

    assert result.skip == 1 and result.skip_md5 == 1  # 缓存里的 MD5 仍参与比对
    assert SEASON1 not in calls  # 子目录零网盘调用（也不触发旧格式重拉）


def test_baidu_subdir_credential_expired_propagates():
    """验收 5（百度）：子目录扫描撞上凭据失效 → 原样上抛，不吞。"""
    table = {"/媒体库/剧名": [_bd_dir(SEASON1, "Season 1")]}

    def raise_or_list(d):
        if d == SEASON1:
            raise CredentialExpired("百度 Cookie 已失效")
        return table[d]

    cli = BaiduClient.__new__(BaiduClient)
    cli.list_dir = raise_or_list
    with pytest.raises(CredentialExpired):
        cli._dir_baselines("/媒体库/剧名", cands=[ShareFile(fid="1", name="EP01.mkv")])


# ---------- 夸克 ----------


def _quark_table() -> dict:
    return {
        "0": [
            {"fid": "m", "file_name": "媒体库", "dir": True, "size": 0},
            {"fid": "s", "file_name": "待整理", "dir": True, "size": 0},
        ],
        "m": [{"fid": "j", "file_name": "剧名", "dir": True, "size": 0}],
        "j": [{"fid": "ss", "file_name": "Season 1", "dir": True, "size": 0}],
        "ss": [{"fid": "f1", "file_name": "EP02.mkv", "dir": False, "size": 20}],
        "s": [],
    }


def test_quark_save_files_baseline_includes_compare_path_subdir():
    """验收 1/2（夸克）：compare_path 进基线（此前完全没读）且递归一层——
    文件在剧名/Season 1/ 里，按名字跳过。"""
    calls: list = []
    ad = _quark(_quark_table(), calls)
    logs: list = []
    spec = TaskSpec(share_url="https://pan.quark.cn/s/abc123def", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="9", fid_token="t", name="EP02.mkv", path="/EP02.mkv", size=20)]

    result = ad.save_files(files, spec, lambda p: None, logs.append)

    assert result.skip == 1 and result.add == 0
    assert calls.count("ss") == 1  # Season 1（fid=ss）被下钻了一次
    assert any("扫描了" in m and "个目录（含子目录）" in m for m in logs)


def test_quark_compare_path_missing_creates_nothing():
    """验收 7（夸克）：compare_path 不存在 → 不参与基线即可，绝不新建目录
    （_req 哨兵被触发即测试失败——建目录必须走 _req POST）。"""
    calls: list = []
    ad = _quark({"0": [], "s": []}, calls)
    logs: list = []
    st: dict = {}

    names = ad._dir_baseline("/媒体库/不存在的剧", create=False, on_log=logs.append, stats=st)

    assert names == set() and st["dirs"] == 0
    assert any("不参与去重基线" in m for m in logs)


def test_quark_lazy_no_subdir_request_when_level1_hits():
    """验收 3（夸克）：一层全部命中 → 子目录零请求。"""
    table = _quark_table()
    table["j"] = [
        {"fid": "f0", "file_name": "EP02.mkv", "dir": False, "size": 20},
        {"fid": "ss", "file_name": "Season 1", "dir": True, "size": 0},
    ]
    calls: list = []
    ad = _quark(table, calls)
    spec = TaskSpec(share_url="https://pan.quark.cn/s/abc123def", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="9", fid_token="t", name="EP02.mkv", path="/EP02.mkv", size=20)]

    result = ad.save_files(files, spec, lambda p: None, lambda m: None)

    assert result.skip == 1
    assert "ss" not in calls


def test_quark_compare_path_read_error_skips_base():
    """F2 验收 1：compare_path 读取抛 AdapterError → 记日志跳过该 base，任务照常完成。"""
    table = {
        "0": [
            {"fid": "m", "file_name": "媒体库", "dir": True, "size": 0},
            {"fid": "s", "file_name": "待整理", "dir": True, "size": 0},
        ],
        "m": AdapterError("网络异常"),  # compare_path 下钻第一层就抖
        "s": [{"fid": "f0", "file_name": "EP02.mkv", "dir": False, "size": 20}],
    }
    calls: list = []
    ad = _quark(table, calls)
    logs: list = []
    spec = TaskSpec(share_url="https://pan.quark.cn/s/abc123def", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="9", fid_token="t", name="EP02.mkv", path="/EP02.mkv", size=20)]

    result = ad.save_files(files, spec, lambda p: None, logs.append)

    assert result.skip == 1  # save_dir 基线照常命中，整单没有失败
    assert any("该目录不参与去重基线" in m for m in logs)


def test_quark_compare_path_credential_expired_raises():
    """F2 验收 2：compare_path 读取撞凭据失效 → 原样上抛。"""
    table = {
        "0": [{"fid": "m", "file_name": "媒体库", "dir": True, "size": 0}],
        "m": CredentialExpired("夸克 Cookie 已失效（401）"),
    }
    calls: list = []
    ad = _quark(table, calls)
    spec = TaskSpec(share_url="https://pan.quark.cn/s/abc123def", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="9", fid_token="t", name="EP02.mkv", path="/EP02.mkv", size=20)]

    with pytest.raises(CredentialExpired):
        ad.save_files(files, spec, lambda p: None, lambda m: None)


def test_baidu_md5_hit_covers_name_miss_no_recursion():
    """F3 验收 2：候选名字未命中但 md5 已在本层命中 → 该文件算已覆盖，不下钻。"""
    table = {
        "/媒体库/剧名": [
            _bd_file("/媒体库/剧名/QMS改名.mkv", "QMS改名.mkv"),  # 名字没命中，但 MD5 相同
            _bd_dir(SEASON1, "Season 1"),
        ],
        SEASON1: [],  # 若误下钻会列它——不得发生
        "/待整理": [],
    }
    calls: list = []
    cli = _baidu(table, calls)
    spec = TaskSpec(share_url="https://pan.baidu.com/s/1abcDEF-_123456789012", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="123", name="EP01.mkv", path="EP01.mkv", size=10, md5=MD5)]

    result = cli.save_files(files, spec, lambda p: None, lambda m: None)

    assert result.skip == 1 and result.skip_md5 == 1
    assert calls == ["/媒体库/剧名", "/待整理"]  # 零子目录请求


def test_baidu_subdir_circuit_open_skips_and_charges_budget():
    """F3 验收 1（百度）：子目录读取撞熔断 → 跳过该子目录、不抛异常、预算照扣。"""
    table = {
        "/base": [_bd_dir("/base/S1", "S1"), _bd_dir("/base/S2", "S2")],
        "/base/S1": CircuitOpen("百度 连续失败熔断中"),
        "/base/S2": [],
    }
    calls: list = []
    cli = _baidu(table, calls)
    logs: list = []
    st: dict = {}
    budget = [3]

    names, _ = cli._dir_baselines("/base", cands=[ShareFile(fid="1", name="X.mkv")], budget=budget, on_log=logs.append, stats=st)

    assert names == set()
    assert st["dirs"] == 2 and not st.get("stopped")  # base + S2（S1 失败不计扫描数）
    assert budget == [1]  # S1 失败 + S2 成功，两次真打了网盘各扣一次
    assert any("读取失败" in m for m in logs)


def test_quark_subdir_scan_stops_at_limit():
    """验收 4（夸克）：子目录超上限 → 停止递归、不抛异常。"""
    table = {
        "0": [{"fid": "m", "file_name": "媒体库", "dir": True, "size": 0}],
        "m": [{"fid": f"d{i}", "file_name": f"S{i}", "dir": True, "size": 0} for i in range(1, 6)],
    }
    for i in range(1, 6):
        table[f"d{i}"] = []
    calls: list = []
    ad = _quark(table, calls)
    logs: list = []
    st: dict = {}

    names = ad._dir_baseline("/媒体库", create=False, cand_names={"X.mkv"}, budget=[3], on_log=logs.append, stats=st)

    assert names == {"S1", "S2", "S3", "S4", "S5"}  # base 一层的子目录名仍在（沿用含目录名口径）
    assert st["dirs"] == 4 and st["stopped"] is True
    assert [c for c in calls if c.startswith("d")] == ["d1", "d2", "d3"]
    assert any("因上限停止递归" in m for m in logs)


# ---------- 115 ----------


def _p115_table() -> dict:
    return {
        "0": [
            {"cid": "m", "n": "媒体库"},
            {"cid": "s", "n": "待整理"},
        ],
        "m": [{"cid": "j", "n": "剧名"}],
        "j": [{"cid": "ss", "n": "Season 1"}],
        "ss": [{"cid": "f3", "n": "EP03.mkv", "s": 5, "sha1": "x"}],
        "s": [],
    }


def test_115_list_dir_names_recurses_one_level():
    """验收 1（115）：文件在剧名/Season 1/ 里 → 惰性下钻收进基线。"""
    calls: list = []
    ad = _p115(_p115_table(), calls)

    names = ad.list_dir_names("/媒体库/剧名", cand_names={"EP03.mkv"})

    assert "EP03.mkv" in names
    assert calls.count("ss") == 1
    # 契约调用（无候选）只扫一层
    calls.clear()
    plain = ad.list_dir_names("/媒体库/剧名")
    assert plain == set() and "ss" not in calls


def test_115_lazy_no_subdir_request_when_level1_hits():
    """验收 3（115）：一层全部命中 → 子目录零请求。"""
    table = _p115_table()
    table["j"] = [
        {"cid": "f3", "n": "EP03.mkv", "s": 5, "sha1": "x"},
        {"cid": "ss", "n": "Season 1"},
    ]
    calls: list = []
    ad = _p115(table, calls)

    names = ad.list_dir_names("/媒体库/剧名", cand_names={"EP03.mkv"})

    assert names == {"EP03.mkv"}
    assert "ss" not in calls


def test_115_subdir_scan_stops_at_limit():
    """验收 4（115）：子目录超上限 → 停止递归、不抛异常。"""
    table = {
        "0": [{"cid": "m", "n": "媒体库"}],
        "m": [{"cid": f"c{i}", "n": f"S{i}"} for i in range(1, 6)],
    }
    for i in range(1, 6):
        table[f"c{i}"] = []
    calls: list = []
    ad = _p115(table, calls)
    logs: list = []
    st: dict = {}

    ad.list_dir_names("/媒体库", cand_names={"X.mkv"}, budget=[3], on_log=logs.append, stats=st)

    assert st["dirs"] == 4 and st["stopped"] is True
    assert [c for c in calls if c.startswith("c")] == ["c1", "c2", "c3"]
    assert any("因上限停止递归" in m for m in logs)


def test_115_subdir_circuit_open_skips():
    """F3 验收 1（115）：子目录读取撞熔断 → 跳过、不抛、预算照扣，其余子目录照扫。"""
    table = {
        "0": [{"cid": "m", "n": "媒体库"}],
        "m": [{"cid": "c1", "n": "S1"}, {"cid": "c2", "n": "S2"}],
        "c1": CircuitOpen("115 连续失败熔断中"),
        "c2": [],
    }
    calls: list = []
    ad = _p115(table, calls)
    logs: list = []
    st: dict = {}
    budget = [3]

    ad.list_dir_names("/媒体库", cand_names={"X.mkv"}, budget=budget, on_log=logs.append, stats=st)

    assert st["dirs"] == 2 and not st.get("stopped")  # base + c2（c1 失败不计）
    assert budget == [1]  # c1 失败 + c2 成功，各扣一次
    assert any("读取失败" in m for m in logs)


# ---------- F4 · 目录缓存 key 账号段对齐 ----------


def test_adapter_acc_key_separates_cache_entries():
    """F4 验收 1：不同 acc_key 的适配器各写各的缓存 key——互不覆盖，一个命中
    不会让另一个命中（各自由 miss 到命中，全程网盘侧可观测）。"""
    table = {
        "/媒体库/剧名": [_bd_dir(SEASON1, "Season 1")],
        SEASON1: [_bd_file(SEASON1 + "/EP01.mkv", "EP01.mkv")],
    }
    calls1: list = []
    cli_main = _baidu(table, calls1)  # 未注入 acc_key → 默认 "main"
    calls2: list = []
    cli_acc2 = _baidu(table, calls2)
    cli_acc2.acc_key = "2"  # 模拟 make_adapter_for 对 acc_id=2 的注入
    cands = [ShareFile(fid="123", name="EP01.mkv", path="EP01.mkv", size=10, md5=MD5)]

    cli_main._dir_baselines("/媒体库/剧名", cands=cands)
    assert calls1.count(SEASON1) == 1  # "main" key miss → 真列了一次

    cli_acc2._dir_baselines("/媒体库/剧名", cands=cands)
    assert calls2.count(SEASON1) == 1  # "2" key 不受 "main" 命中影响，仍 miss 真列

    calls1.clear()
    calls2.clear()
    cli_main._dir_baselines("/媒体库/剧名", cands=cands)
    cli_acc2._dir_baselines("/媒体库/剧名", cands=cands)
    assert SEASON1 not in calls1 and SEASON1 not in calls2  # 各自命中自己的 key

    accs = {e["acc"] for e in dir_cache.list_entries() if e["type"] == "baidu"}
    assert accs == {"main", "2"}  # 两个账号段各有一份，互不覆盖


def test_adapter_without_acc_key_defaults_main():
    """F4 验收 2：实例上没注入 acc_key（__new__ 出来的裸实例）→ 默认走 "main"，
    缓存落在 "main" 段（老行为不回归）。"""
    table = {"/媒体库/剧名": [_bd_dir(SEASON1, "Season 1")], SEASON1: []}
    calls: list = []
    cli = _baidu(table, calls)

    cli._dir_baselines("/媒体库/剧名", cands=[ShareFile(fid="1", name="X.mkv")])

    assert cli.acc_key == "main"
    entries = [e for e in dir_cache.list_entries() if e["type"] == "baidu"]
    assert entries and all(e["acc"] == "main" for e in entries)


def test_make_adapter_for_injects_acc_key():
    """F4 目标 1：make_adapter_for 注入口径与 cache_api.list_files 一致——
    传 acc_id 用 str(acc_id)，缺省回落第一个账号记 "main"。

    用 "115" 测：共享测试库里 quark/baidu 已有 test_pk_ai 导入期种的无凭据账号
    （回落分支会先撞到它们），115 没有种子，回落必命中我们自己的账号。"""
    from app.db import SessionLocal
    from app.deps import make_adapter_for
    from app.models import Account
    from app.security import encrypt_credential

    with SessionLocal() as db:
        db.add(Account(type="115", cookies_enc=encrypt_credential("UID=1"), status="connected"))
        db.add(Account(type="115", cookies_enc=encrypt_credential("UID=2"), status="connected"))
        db.commit()
        # 只认自己刚建的两行——别人的种子（若有）不碰，删也只删自己的
        ids = sorted(r.id for r in db.query(Account).filter(Account.type == "115", Account.cookies_enc != "").all())
        assert len(ids) == 2
        try:
            a_default = make_adapter_for(db, "115")  # 缺省 → 回落第一个账号
            a_first = make_adapter_for(db, "115", ids[0])  # 显式传第一个账号 id
            a_second = make_adapter_for(db, "115", ids[1])  # 显式传第二个账号 id

            assert a_default.acc_key == "main"
            assert a_first.acc_key == str(ids[0])
            assert a_second.acc_key == str(ids[1])
        finally:
            # 只删自己建的两行——共享临时库里别的测试的种子账号不能动
            # （曾按 type 全删，把 test_pk_ai 依赖的百度种子号删掉引发其 502 回归）
            db.query(Account).filter(Account.id.in_(ids)).delete()
            db.commit()


# ---------- T-007 · 转存前确保目标目录存在 ----------


def test_baidu_save_dir_missing_created_before_transfer():
    """T-007 验收（百度）：目标目录不存在 → 探测缺失后建一次（留痕），建好才提交
    转存；目录存在时零建目录请求。"""
    table = {"/媒体库/剧名": []}  # compare 为空 → need 非空；/待整理 缺失
    calls: list = []
    cli = _baidu(table, calls)
    mkdirs: list = []
    transfers: list = []

    def fake_mkdir(p):
        mkdirs.append(p)

    def fake_transfer(ctx, group, target, spec, result, on_log):
        transfers.append(target)
        result.add += len(group)

    cli._mkdir = fake_mkdir
    cli._transfer_group = fake_transfer
    logs: list = []
    spec = TaskSpec(share_url="https://pan.baidu.com/s/1abcDEF-_123456789012", save_dir="/待整理", compare_path="/媒体库/剧名")
    files = [ShareFile(fid="123", name="EP07.mkv", path="EP07.mkv", size=10, md5="ff" * 16)]

    result = cli.save_files(files, spec, lambda p: None, logs.append)

    assert mkdirs == ["/待整理"]  # 确认缺失才建、只建一次
    assert transfers == ["/待整理"]  # 建好之后才拿它提交转存
    assert result.add == 1
    assert any("目标目录 /待整理 不存在，已自动创建" in m for m in logs)

    # 目录已存在（下一轮）：不再试建、不再留痕
    mkdirs.clear()
    logs.clear()
    cli2 = _baidu({"/媒体库/剧名": [], "/待整理": []}, calls := [])
    cli2._mkdir = fake_mkdir
    cli2._transfer_group = fake_transfer
    cli2.save_files(files, spec, lambda p: None, logs.append)
    assert mkdirs == [] and not any("已自动创建" in m for m in logs)


def test_quark_save_dir_missing_created_before_transfer():
    """T-007 验收（夸克）：目标目录不存在 → 软解析判缺失、建一次留痕，转存用的
    就是新建目录的 fid。"""
    table = {"0": []}  # 根层空 → /新目录 解析不到（缺失）
    calls: list = []
    ad = _quark(table, calls)
    ensures: list = []
    batches: list = []

    def fake_ensure_dir(p):
        ensures.append(p)
        return "newfid"

    def fake_save_batch(batch, to_fid, spec, result, on_log):
        batches.append((list(batch), to_fid))
        result.add += len(batch)

    ad.ensure_dir = fake_ensure_dir
    ad._save_batch = fake_save_batch
    logs: list = []
    spec = TaskSpec(share_url="https://pan.quark.cn/s/abc123def", save_dir="/新目录")
    files = [ShareFile(fid="9", fid_token="t", name="EP08.mkv", path="/EP08.mkv", size=9)]

    result = ad.save_files(files, spec, lambda p: None, logs.append)

    assert ensures == ["/新目录"]  # 缺失才建一次（转存目标通道）
    assert batches and batches[0][1] == "newfid"  # 转存提交用的就是新建目录的 fid
    assert result.add == 1
    assert any("目标目录 /新目录 不存在，已自动创建" in m for m in logs)


def test_115_save_dir_missing_created_before_transfer():
    """T-007 验收（115）：目标目录不存在 → path_to_cid 判缺失、建一次留痕，
    整组接收落在新建目录的 cid 上。"""
    table = {"0": []}
    calls: list = []
    ad = _p115(table, calls)
    ad._share_ctx = {"share_code": "s1", "receive_code": "r1"}  # save_files 前置条件
    ensures: list = []
    receives: list = []

    def fake_ensure_dir(p):
        ensures.append(p)
        return "cid9"

    def fake_receive(ctx, group, cid, spec, result, on_log, **kw):
        receives.append((list(group), cid))
        result.add += len(group)

    ad.ensure_dir = fake_ensure_dir
    ad._receive_group = fake_receive
    logs: list = []
    spec = TaskSpec(share_url="https://115.com/s/abc?password=x", save_dir="/新目录")
    files = [ShareFile(fid="5", name="EP09.mkv", path="/EP09.mkv", size=9)]

    result = ad.save_files(files, spec, lambda p: None, logs.append)

    assert ensures == ["/新目录"]
    assert receives and receives[0][1] == "cid9"
    assert result.add == 1
    assert any("目标目录 /新目录 不存在，已自动创建" in m for m in logs)


# ---------- T-004 · 百度 filemanager opera 参数修正 ----------


def test_baidu_filemanager_opera_params():
    """T-004 验收：rename_dir / _apply_renames 带 opera=rename（query）。
    （delete_dir 已改走 PCS 通道，见 test_baidu_delete_dir_uses_pcs_channel —— T-004-F1。）"""
    cli = BaiduClient.__new__(BaiduClient)
    captured: list[dict] = []

    def fake_share_post(url, *, params, data, referer):
        captured.append({"params": params, "data": data})
        return {"errno": 0, "info": [{"errno": 0, "to_fid": "7"}]}

    cli._share_post = fake_share_post

    cli.rename_dir("/a/旧", "新")
    assert captured[-1]["params"]["opera"] == "rename"

    group = [ShareFile(fid="1", name="old.mkv", target_name="new.mkv")]
    cli._apply_renames(group, "/a", TaskSpec(share_url="https://pan.baidu.com/s/x"), TransferResult(), lambda m: None)
    assert captured[-1]["params"]["opera"] == "rename"


def test_baidu_delete_dir_uses_pcs_channel():
    """T-004-F1：目录删除走 PCS delete（与 _mkdir 同通道）——filemanager 的
    opera=delete 实测全组合 8001/132 不可用，别再改回去。"""
    cli = BaiduClient.__new__(BaiduClient)
    calls: list[dict] = []

    def fake_get(url, params=None):
        calls.append({"url": url, "params": dict(params or {})})
        return {"errno": 0}

    cli._get = fake_get
    cli.delete_dir("/a/旧目录")

    assert len(calls) == 1  # 只发一发 PCS GET
    assert calls[0]["url"] == "https://pcs.baidu.com/rest/2.0/pcs/file"
    assert calls[0]["params"]["method"] == "delete"
    assert calls[0]["params"]["app_id"] == 778750
    assert calls[0]["params"]["path"] == "/a/旧目录"

    # errno 语义：12（不存在，幂等）/ None 照样成功；其余按原格式报错
    for ok in (12, None):
        cli._get = lambda url, params=None: {"errno": ok}
        cli.delete_dir("/a")
    # T-013①：凭据失效码必须抛 CredentialExpired（标记账号 + 推过期告警），与 _mkdir 同口径
    for expired in (-6, 31041, 31042):
        cli._get = lambda url, params=None: {"errno": expired}
        with pytest.raises(CredentialExpired):
            cli.delete_dir("/a")
    cli._get = lambda url, params=None: {"errno": 8}
    with pytest.raises(AdapterError, match="删除失败：errno=8"):
        cli.delete_dir("/a")


def test_baidu_filemanager_error_keeps_errno_format():
    """T-004：失败时错误信息保留原格式（errno 原样带出），方便排查。"""
    cli = BaiduClient.__new__(BaiduClient)

    def fake_share_post(url, *, params, data, referer):
        return {"errno": 2}

    cli._share_post = fake_share_post

    with pytest.raises(AdapterError, match="errno=2"):
        cli.rename_dir("/a/旧", "新")

    logs: list = []
    group = [ShareFile(fid="1", name="old.mkv", target_name="new.mkv")]
    cli._apply_renames(group, "/a", TaskSpec(share_url="x"), TransferResult(), logs.append)
    assert any("重命名失败 old.mkv → new.mkv：errno=2" in m for m in logs)
