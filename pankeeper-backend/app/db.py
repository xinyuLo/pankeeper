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
            ("ban_reason", "TEXT DEFAULT ''"),
            ("compare_path", "TEXT DEFAULT ''"),
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
