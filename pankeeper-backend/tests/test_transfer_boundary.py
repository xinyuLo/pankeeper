"""T-012 · transfer 包边界结构测试：manual↔auto 互不依赖、core/targets 不反向依赖两者。

用 AST 解析真实源文件（注释/字符串不参与），规则只看 import 方向；
__init__.py 作为组合根允许 import 两个流程模块（它就是干这个的）。
检查函数独立可测：喂构造的违规源码必须返回违规——不许写出永远绿的断言。
"""
from __future__ import annotations

import ast
from pathlib import Path

TRANSFER_DIR = Path(__file__).resolve().parent.parent / "app" / "transfer"

# 文件 → 禁止 import 的模块名（相对/绝对、from/import 两种形态都查）
BANNED = {
    "manual.py": {"auto"},
    "auto.py": {"manual"},
    "core.py": {"auto", "manual"},
    "targets.py": {"auto", "manual"},
}


def import_violations(source: str, banned: set[str]) -> list[str]:
    """AST 扫源码，返回违反方向的 import 描述（空列表 = 干净）。

    命中形态：`from .auto import x` / `from ..transfer.auto import x` /
    `from app.transfer.auto import x` / `import auto` / `import app.transfer.auto`。
    不误伤：`from .targets import x`、`from ..services import qms`、`import automatic_x`。"""
    bad: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            tail = mod.split(".")[-1] if mod else ""
            if node.level > 0:
                hit = tail in banned  # 相对导入：from .<banned> / from ..x.<banned>
            else:
                parts = mod.split(".")
                hit = "transfer" in parts and tail in banned  # 绝对导入只认本包路径
            if hit:
                bad.append(f"line {node.lineno}: from {'.' * node.level}{mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[-1] in banned and ("transfer" in parts or len(parts) == 1):
                    bad.append(f"line {node.lineno}: import {alias.name}")
    return bad


def test_transfer_boundary_rules():
    """真实源文件全部干净：manual 不 import auto、auto 不 import manual、
    core/targets 两个共享模块谁都不 import（防循环）。"""
    for fname, banned in BANNED.items():
        path = TRANSFER_DIR / fname
        assert path.exists(), f"app/transfer/{fname} 缺失"
        violations = import_violations(path.read_text(encoding="utf-8"), banned)
        assert violations == [], f"{fname} 违反包边界: {violations}"


def test_checker_actually_fails_on_violations():
    """可失败证明（验收 4 的机制面）：同一检查函数喂违规源码必须抓到——
    三种违规形态逐条命中，干净源码零误报。"""
    bad_src = "from .auto import dir_only_video\nimport app.transfer.auto\nimport auto\n"
    assert len(import_violations(bad_src, {"auto"})) == 3

    assert import_violations("from .manual import _x\n", {"manual"})  # 对向同样能抓
    assert import_violations("import manual\n", {"manual"})

    clean = (
        "from .targets import dir_only_video\n"
        "from ..services import qms\n"
        "from .core import push_log\n"
        "import automatic_tool\n"
        "from app.adapters import factory\n"
    )
    assert import_violations(clean, {"auto", "manual"}) == []
