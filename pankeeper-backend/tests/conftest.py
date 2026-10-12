"""测试隔离：PK_DATA 指到临时目录，pytest 绝不碰 data/pankeeper.db（开发/部署库）。

必须在 app 模块导入前设置——app/config.py 在 import 时就解析 DATA_DIR；
没有这层隔离，test_security 之类的设置写入测试会把开发库里的真实配置覆盖掉
（2026-10-12 实锤：notify 迁移测试把 dev 库的 sendkey 覆盖成了测试值）。
"""
import os
import tempfile

if not os.environ.get("PK_DATA"):  # 已显式指定的尊重调用方
    os.environ["PK_DATA"] = tempfile.mkdtemp(prefix="pk-test-")

from app.db import init_db  # noqa: E402 —— 必须在环境变量就位后再导入

init_db()  # 临时库是空的，建好表测试才能直接用 SessionLocal
