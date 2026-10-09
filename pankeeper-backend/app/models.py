from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

def now_str() -> str:
    return datetime.now().strftime("%m-%d %H:%M")

class Account(Base):

    __tablename__ = "drive_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text)
    alias: Mapped[str] = mapped_column(Text, default="")
    cookies_enc: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="unset")
    nickname: Mapped[str] = mapped_column(Text, default="")
    last_check: Mapped[str] = mapped_column(Text, default="从未配置")

    @property
    def display_name(self) -> str:
        return self.alias or self.nickname or f"{self.type}#{self.id}"

class RequestStat(Base):

    __tablename__ = "request_stat"

    date: Mapped[str] = mapped_column(Text, primary_key=True)
    drive: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)

class PaTask(Base):
    __tablename__ = "pa_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, default="baidu")
    acc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    share_url: Mapped[str] = mapped_column(Text, default="")
    share_code: Mapped[str] = mapped_column(Text, default="")
    save_dir: Mapped[str] = mapped_column(Text, default="")
    compare_path: Mapped[str] = mapped_column(Text, default="")
    include_subdirs: Mapped[bool] = mapped_column(Boolean, default=True)
    cron: Mapped[str] = mapped_column(Text, default="")
    exclude_json: Mapped[str] = mapped_column(Text, default="[]")
    exclude_md5_json: Mapped[str] = mapped_column(Text, default="[]")
    exclude_count: Mapped[int] = mapped_column(Integer, default=0)
    qms_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    lp_event: Mapped[str] = mapped_column(Text, default="")
    regex_pattern: Mapped[str] = mapped_column(Text, default="")
    regex_replace: Mapped[str] = mapped_column(Text, default="")
    drill_on: Mapped[bool] = mapped_column(Boolean, default=False)
    drill_json: Mapped[str] = mapped_column(Text, default="[]")
    post_qms: Mapped[bool] = mapped_column(Boolean, default=False)
    post_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[str] = mapped_column(Text, default="")
    last_status: Mapped[str] = mapped_column(Text, default="never")
    last_result: Mapped[str] = mapped_column(Text, default="—")
    ban_reason: Mapped[str] = mapped_column(Text, default="")

class DdItem(Base):

    __tablename__ = "dd_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text)
    account: Mapped[str] = mapped_column(Text, default="main")
    sort: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    qms_on: Mapped[bool] = mapped_column(Boolean, default=False)
    qms_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    lp_on: Mapped[bool] = mapped_column(Boolean, default=False)
    lp_event: Mapped[str] = mapped_column(Text, default="")

    media_type: Mapped[str] = mapped_column(Text, default="")

    only_video: Mapped[bool] = mapped_column(Boolean, default=True)

class QmsPath(Base):
    __tablename__ = "qms_paths"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)

class StrmPath(Base):
    __tablename__ = "strm_paths"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_path: Mapped[str] = mapped_column(Text)

class QueueTaskRow(Base):

    __tablename__ = "queue_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, default="/")
    files: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[str] = mapped_column(Text, default="—")
    status: Mapped[str] = mapped_column(Text, default="wait")
    phase: Mapped[str] = mapped_column(Text, default="")
    phase_start: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    done_at: Mapped[int] = mapped_column(Integer, default=0)
    logs_json: Mapped[str] = mapped_column(Text, default="[]")

    share_url: Mapped[str] = mapped_column(Text, default="")
    share_code: Mapped[str] = mapped_column(Text, default="")
    include_subdirs: Mapped[bool] = mapped_column(Boolean, default=True)

    acc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pa_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exclude_json: Mapped[str] = mapped_column(Text, default="[]")
    exclude_md5_json: Mapped[str] = mapped_column(Text, default="[]")
    regex_pattern: Mapped[str] = mapped_column(Text, default="")

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")

class Record(Base):

    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    n: Mapped[str] = mapped_column(Text)
    t: Mapped[str] = mapped_column(Text)
    p: Mapped[str] = mapped_column(Text)
    st: Mapped[str] = mapped_column(Text)
    cls: Mapped[str] = mapped_column(Text, default="t-ok")
    tm: Mapped[str] = mapped_column(Text)
    qms_json: Mapped[str] = mapped_column(Text, default='{"st":"未执行","cls":"t-off"}')
    strm_json: Mapped[str] = mapped_column(Text, default='{"st":"未执行","cls":"t-off"}')
    share_url: Mapped[str] = mapped_column(Text, default="")
    share_code: Mapped[str] = mapped_column(Text, default="")

    source: Mapped[str] = mapped_column(Text, default="search")

    backend: Mapped[str] = mapped_column(Text, default="")
    cron: Mapped[str] = mapped_column(Text, default="")
    include_subdirs: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_count: Mapped[int] = mapped_column(Integer, default=0)
    post_qms: Mapped[bool] = mapped_column(Boolean, default=False)
    post_notify: Mapped[bool] = mapped_column(Boolean, default=False)
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    files_json: Mapped[str] = mapped_column(Text, default="[]")

class RunHistory(Base):

    __tablename__ = "run_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    started: Mapped[str] = mapped_column(Text)
    finished: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="running")
    add: Mapped[int] = mapped_column(Integer, default=0)
    skip: Mapped[int] = mapped_column(Integer, default=0)
    skip_md5: Mapped[int] = mapped_column(Integer, default=0)
    fail: Mapped[int] = mapped_column(Integer, default=0)
    excl: Mapped[int] = mapped_column(Integer, default=0)
    total_share: Mapped[int] = mapped_column(Integer, default=0)
    regex_miss: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[int] = mapped_column(Integer, default=0)
    transferred_json: Mapped[str] = mapped_column(Text, default="[]")
    excluded_json: Mapped[str] = mapped_column(Text, default="[]")
    regex_hit_json: Mapped[str] = mapped_column(Text, default="[]")

    md5_skipped_json: Mapped[str] = mapped_column(Text, default="[]")

    qms_json: Mapped[str] = mapped_column(Text, default="")
    strm_json: Mapped[str] = mapped_column(Text, default="")
    logs_json: Mapped[str] = mapped_column(Text, default="[]")

class SearchHistory(Base):
    __tablename__ = "search_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(Text)
    cloud_types: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="[]")
    fetched_at: Mapped[int] = mapped_column(Integer, default=0)

class DirPathCache(Base):

    __tablename__ = "dir_path_cache"
    account_type: Mapped[str] = mapped_column(Text, primary_key=True)
    dir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dir_path: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[str] = mapped_column(Text, default="")
    last_seen_at: Mapped[int] = mapped_column(Integer, default=0)

class DirTreeCacheRow(Base):

    __tablename__ = "dir_tree_cache"
    type: Mapped[str] = mapped_column(Text, primary_key=True)
    acc: Mapped[str] = mapped_column(Text, primary_key=True)
    cid: Mapped[str] = mapped_column(Text, primary_key=True)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[int] = mapped_column(Integer, default=0)
    cached_at: Mapped[int] = mapped_column(Integer, default=0)

class ShareListCacheRow(Base):

    __tablename__ = "share_list_cache"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    cached_at: Mapped[int] = mapped_column(Integer, default=0)

class PushLog(Base):

    __tablename__ = "push_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(Text, default="info")
    status: Mapped[str] = mapped_column(Text, default="success")
    error: Mapped[str] = mapped_column(Text, default="")

    content: Mapped[str] = mapped_column(Text, default="")
