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
    from . import models

    Base.metadata.create_all(engine)
    _migrate_columns()
    _backfill_litepan_backend()
    _migrate_accounts()

def _migrate_accounts() -> None:
    with engine.connect() as conn:
        old = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchall()
        if not old:
            return
        new_rows = conn.exec_driver_sql("SELECT COUNT(*) FROM drive_accounts").fetchone()[0]
        if new_rows:
            return
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

def _backfill_litepan_backend() -> None:
    with engine.connect() as conn:
        n = conn.exec_driver_sql(
            "UPDATE records SET backend='litepan' "
            "WHERE (backend IS NULL OR backend='') "
            "AND logs_json LIKE '%LitePan%'"
        ).rowcount
        conn.commit()
    if n:
        print(f"[migrate] 旧记录 LitePan backend 回填：{n} 条")

def _migrate_columns() -> None:
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
            ("source", "TEXT DEFAULT 'search'"),
            ("backend", "TEXT DEFAULT ''"),
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
            ("md5_skipped_json", "TEXT DEFAULT '[]'"),
            ("qms_json", "TEXT DEFAULT ''"),
            ("strm_json", "TEXT DEFAULT ''"),
        ],
        "queue_tasks": [
            ("acc_id", "INTEGER"),
            ("pa_task_id", "INTEGER"),
            ("exclude_json", "TEXT DEFAULT '[]'"),
            ("exclude_md5_json", "TEXT DEFAULT '[]'"),
            ("regex_pattern", "TEXT DEFAULT ''"),
        ],
        "push_logs": [
            ("content", "TEXT DEFAULT ''"),
        ],
        "dd_items": [
            ("lp_on", "INTEGER DEFAULT 0"),
            ("lp_event", "TEXT DEFAULT ''"),
            ("media_type", "TEXT DEFAULT ''"),
            ("only_video", "INTEGER DEFAULT 1"),
        ],
        "pa_tasks": [
            ("lp_event", "TEXT DEFAULT ''"),
        ],
    }
    with engine.connect() as conn:
        for table, columns in plan.items():
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            if not rows:
                continue
            existing = {r[1] for r in rows}
            for name, ddl in columns:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

        try:
            conn.exec_driver_sql(
                "UPDATE records SET source='auto' WHERE source<>'auto' AND share_url<>''"
                " AND share_url IN (SELECT share_url FROM pa_tasks WHERE share_url<>'')"
            )
        except Exception as e:
            print(f"[migrate] 记录来源回填跳过：{e}")
        conn.commit()

def db_session() -> Session:
    return SessionLocal()
