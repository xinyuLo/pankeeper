"""ShareLocked 分类单测：「链接活着但要提取码/访问码」必须抛 ShareLocked（非死链），
夸克/115 的 share_code 回落必须生效（2026-10-15 推送轰炸实锤的修复面）。

运行：python -m pytest tests/test_share_locked.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.base import AdapterError, RiskControlError, ShareBanned, ShareLocked, TaskSpec  # noqa: E402
from app.adapters.baidu import BaiduClient  # noqa: E402
from app.adapters.pan115 import Pan115Adapter  # noqa: E402
from app.adapters.quark import QuarkAdapter  # noqa: E402


# ---------- 百度 ----------

def test_baidu_wrong_code_is_locked():
    # errno=-12 = 提取码错误：链接活着，可行动（补码重试），不是死链
    with pytest.raises(ShareLocked):
        BaiduClient._check_share_errno({"errno": -12}, "测试")


def test_baidu_dead_errno_is_banned():
    with pytest.raises(ShareBanned):
        BaiduClient._check_share_errno({"errno": -9}, "测试")


# ---------- 夸克 ----------

def _quark() -> QuarkAdapter:
    return QuarkAdapter.__new__(QuarkAdapter)  # 跳过 __init__（不解密 Cookie，不建连接）


def test_quark_need_code_message_is_locked(monkeypatch):
    q = _quark()
    monkeypatch.setattr(q, "_req", lambda m, u, **kw: {"status": 421, "message": "访问码错误"})
    with pytest.raises(ShareLocked):
        q.get_stoken("pwdid", "")


def test_quark_dead_message_is_banned(monkeypatch):
    q = _quark()
    monkeypatch.setattr(q, "_req", lambda m, u, **kw: {"status": 404, "message": "分享不存在"})
    with pytest.raises(ShareBanned):
        q.get_stoken("pwdid", "")


def test_quark_banned_error_message_is_banned(monkeypatch):
    """2026-10-31 快速转存弹窗实录：夸克封禁分享走的是「code≠0 且 HTTP≠200」分支，
    _req 直接抛 AdapterError「夸克接口错误：分享者用户封禁链接查看受限」，到不了
    status 判定 → check-link 判 uncertain 放行、死链照样开弹窗。get_stoken 必须转 ShareBanned。"""
    q = _quark()

    def boom(m, u, **kw):
        raise AdapterError("夸克接口错误：分享者用户封禁链接查看受限")

    monkeypatch.setattr(q, "_req", boom)
    with pytest.raises(ShareBanned):
        q.get_stoken("pwdid", "")


def test_check_link_banned_message_is_bad(monkeypatch):
    """兜底词表也要认「封禁/查看受限」：适配器没转 ShareBanned 时 check-link 判 bad。"""
    from app.api import search as search_api

    class _Fake:
        def probe_share(self, spec):
            raise AdapterError("夸克接口错误：分享者用户封禁链接查看受限")

    monkeypatch.setattr("app.adapters.factory.make_adapter", lambda t: _Fake())
    res = search_api.check_link(
        {"type": "quark", "url": "https://pan.quark.cn/s/deadbeef20261031", "share_code": ""},
        _user=None,
    )
    assert res["state"] == "bad"


def test_quark_prepare_share_code_fallback(monkeypatch):
    """链接没带 ?pwd= 时回落 spec.share_code；带了以链接为准。"""
    q = _quark()
    seen: dict = {}
    monkeypatch.setattr(q, "get_stoken", lambda pwd_id, passcode: seen.update(passcode=passcode) or "st")
    parsed = q.prepare(TaskSpec(share_url="https://pan.quark.cn/s/abcd1234", share_code="ab12"))
    assert parsed["passcode"] == "ab12" and seen["passcode"] == "ab12"
    parsed = q.prepare(TaskSpec(share_url="https://pan.quark.cn/s/abcd1234?pwd=url99", share_code="ab12"))
    assert parsed["passcode"] == "url99"


# ---------- 115 ----------

def test_115_need_code_error_is_locked():
    # 「分享清单失败：请输入访问码」轰炸案的原错误文案：必须归 locked
    with pytest.raises(ShareLocked):
        Pan115Adapter._check_state({"state": False, "error": "请输入访问码"}, "分享清单")
    with pytest.raises(ShareLocked):
        Pan115Adapter._check_state({"state": False, "error": "密码错误"}, "分享清单")


def test_115_verifycode_is_riskcontrol():
    with pytest.raises(RiskControlError):
        Pan115Adapter._check_state({"state": False, "error": "请输入验证码"}, "分享清单")


def test_115_receive_code_fallback(monkeypatch):
    """链接没带 ?password= 时回落 spec.share_code（115 密码常态就在 URL 里，但搜索源可能剥掉）。"""
    p = Pan115Adapter.__new__(Pan115Adapter)
    seen: dict = {}

    def fake_snap(share_code, receive_code, cid, limit=None, max_pages=100):
        seen["rc"] = receive_code
        return []

    monkeypatch.setattr(p, "_snap_page", fake_snap)
    p.probe_share(TaskSpec(share_url="https://115.com/s/abc", share_code="xy12"))
    assert seen["rc"] == "xy12"
