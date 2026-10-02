"""SQLAlchemy 引擎与会话。

SQLite 开 WAL + busy_timeout：Web 侧读（队列看板轮询）与任务写并发不打架
（bdsavepro 把高频进度回写放 JSON 里丢过数据，这里全部落库 + WAL）。
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DB_URL

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)
    _migrate_columns()
    _migrate_accounts()


def _migrate_accounts() -> None:
    """单账号 → 多账号迁移：旧 accounts 表（type 主键）数据搬入 drive_accounts。

    只在「新表为空且旧表存在」时执行一次；旧表原样保留作回滚保险。
    别名留空（前端回落显示昵称/平台名）。
    """
    with engine.connect() as conn:
        old = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchall()
        if not old:
            return
        new_rows = conn.exec_driver_sql("SELECT COUNT(*) FROM drive_accounts").fetchone()[0]
        if new_rows:
            return  # 已迁移过
        rows = conn.exec_driver_sql(
            "SELECT type, cookies_enc, status, nickname, last_check FROM accounts"
        ).fetchall()
        for r in rows:
            conn.exec_driver_sql(
                "INSERT INTO drive_accounts (type, alias, cookies_enc, status, nickname, last_check)"
                " VALUES (?, '', ?, ?, ?, ?)",
                tuple(r),
            )
        conn.commit()
        print(f"[migrate] 多账号迁移：旧 accounts 表 {len(rows)} 个账号已搬入 drive_accounts（旧表保留）")


def _migrate_columns() -> None:
    """轻量列迁移：create_all 只建新表不加列，老库缺列时在这里补。"""
    plan = {
        "pa_tasks": [
            ("drill_on", "INTEGER DEFAULT 0"),
            ("drill_json", "TEXT DEFAULT '[]'"),
            ("regex_pattern", "TEXT DEFAULT ''"),
            ("regex_replace", "TEXT DEFAULT ''"),
            ("qms_id", "INTEGER"),
            ("strm_id", "INTEGER"),
            ("exclude_json", "TEXT DEFAULT '[]'"),
            ("exclude_md5_json", "TEXT DEFAULT '[]'"),
            ("ban_reason", "TEXT DEFAULT ''"),
            ("compare_path", "TEXT DEFAULT ''"),
            ("acc_id", "INTEGER"),
        ],
        "records": [
            ("files_json", "TEXT DEFAULT '[]'"),
        ],
        "run_history": [
            ("skip_md5", "INTEGER DEFAULT 0"),
            ("total_share", "INTEGER DEFAULT 0"),
            ("regex_miss", "INTEGER DEFAULT 0"),
            ("message", "TEXT DEFAULT ''"),
            ("duration", "INTEGER DEFAULT 0"),
            ("transferred_json", "TEXT DEFAULT '[]'"),
            ("excluded_json", "TEXT DEFAULT '[]'"),
            ("regex_hit_json", "TEXT DEFAULT '[]'"),
        ],
        "queue_tasks": [
            ("acc_id", "INTEGER"),
            ("pa_task_id", "INTEGER"),
            ("exclude_json", "TEXT DEFAULT '[]'"),
            ("exclude_md5_json", "TEXT DEFAULT '[]'"),
        ],
    }
    with engine.connect() as conn:
        for table, columns in plan.items():
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            if not rows:
                continue  # 表还不存在，create_all 会带上全部列
            existing = {r[1] for r in rows}
            for name, ddl in columns:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        conn.commit()


def db_session() -> Session:
    return SessionLocal()
