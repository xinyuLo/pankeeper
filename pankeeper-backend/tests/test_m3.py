"""M3 自动任务链路的单元测试：不联网，只测纯逻辑。"""
from __future__ import annotations

import app.adapters.baidu as bd
from app.adapters.base import ShareBanned, TaskSpec, ShareFile
from app.adapters.baidu import BaiduClient
from app.services.pa_scheduler import run_task  # noqa: F401  确认可导入


def test_parse_share_url_forms():
    # 页面短码返回全长（含前导 1）；verify 用的 22 位码由 _verify_surl 剥
    assert BaiduClient.parse_share_url("https://pan.baidu.com/s/1abcDEF-_/") == "1abcDEF-_"
    assert BaiduClient.parse_share_url("https://pan.baidu.com/s/1abcDEF-_?pwd=xy12") == "1abcDEF-_"
    assert BaiduClient.parse_share_url("https://pan.baidu.com/share/init?surl=abcDEF-_") == "abcDEF-_"
    assert BaiduClient.parse_share_url(" https://pan.baidu.com/s/1xyz12 #comment") == "1xyz12"


def test_verify_surl_strips_leading_one():
    assert BaiduClient._verify_surl("1tg7WGwRWH5MPZp92QWkbGQ") == "tg7WGwRWH5MPZp92QWkbGQ"
    assert BaiduClient._verify_surl("abcDEF-_") == "abcDEF-_"


def test_parse_share_url_rejects_garbage():
    import pytest
    from app.adapters.base import AdapterError

    with pytest.raises(AdapterError):
        BaiduClient.parse_share_url("https://www.example.com/not-a-share")


def test_check_share_errno_buckets():
    import pytest
    from app.adapters.base import AdapterError, CredentialExpired

    BaiduClient._check_share_errno({"errno": 0}, "x")
    with pytest.raises(ShareBanned):
        BaiduClient._check_share_errno({"errno": 145}, "x")
    with pytest.raises(CredentialExpired):
        BaiduClient._check_share_errno({"errno": -6}, "x")
    with pytest.raises(AdapterError):
        BaiduClient._check_share_errno({"errno": -12}, "x")


def test_share_row_to_file_strips_prefix():
    f = BaiduClient._share_row_to_file(
        {"fs_id": 123, "path": "/sharelink1234-567/剧/第1集.mkv", "server_filename": "第1集.mkv",
         "isdir": 0, "size": 10, "md5": "ABCDEF"},
        "剧",
    )
    assert f.fid == "123" and f.name == "第1集.mkv" and f.path == "剧/第1集.mkv"
    assert f.md5 == "abcdef" and not f.is_dir


def test_spec_exclude_and_compare_defaults():
    s = TaskSpec(share_url="https://pan.baidu.com/s/1abc")
    assert s.exclude_names == set() and s.compare_path == ""
    f = ShareFile(fid="1", name="a.mp4", path="a.mp4", md5="")
    assert f.md5 == "" and f.target_name is None


def test_baidu_client_has_share_interface():
    # 不构造实例（会解密凭据），只验证类实现了 CloudAdapter 全套接口
    for m in ("verify", "list_share", "list_dir_names", "save_files", "summary"):
        assert hasattr(bd.BaiduClient, m)
    assert issubclass(BaiduClient, object)


# ---------- 115 adapter ----------

from app.adapters.pan115 import Pan115Adapter, parse_share_url  # noqa: E402


def test_115_parse_share_url():
    assert parse_share_url("https://115.com/s/abcXYZ?password=q1w2") == ("abcXYZ", "q1w2")
    assert parse_share_url("https://115cdn.com/s/abcXYZ") == ("abcXYZ", "")
    assert parse_share_url("https://anxia.com/s/abcXYZ?password=pw") == ("abcXYZ", "pw")

    import pytest
    from app.adapters.base import AdapterError

    with pytest.raises(AdapterError):
        parse_share_url("https://pan.baidu.com/s/xxxx")


def test_115_row_to_file_fid_priority():
    # 文件夹：有 fid 无哈希 → is_dir，fid 优先
    d = Pan115Adapter._row_to_file({"fid": "f1", "cid": "p", "n": "剧集"}, "")
    assert d.is_dir and d.fid == "f1" and d.path == "剧集"
    # 文件：无 fid 取 cid，有 sha 判文件
    f = Pan115Adapter._row_to_file({"cid": "c9", "n": "a.mkv", "s": 5, "sha": "abc"}, "剧集")
    assert not f.is_dir and f.fid == "c9" and f.path == "剧集/a.mkv" and f.size == 5


def test_115_check_state_buckets():
    import pytest
    from app.adapters.base import AdapterError, CredentialExpired

    assert Pan115Adapter._check_state({"state": True}, "x") is False
    # 「已接收」幂等成功
    assert Pan115Adapter._check_state({"state": False, "error": "文件已接收，无需重复接收"}, "x", already_ok=True) is True
    with pytest.raises(AdapterError):
        Pan115Adapter._check_state({"state": False, "error": "触发验证码，请完成验证"}, "x")
    with pytest.raises(CredentialExpired):
        Pan115Adapter._check_state({"state": False, "error": "请先登录"}, "x")
