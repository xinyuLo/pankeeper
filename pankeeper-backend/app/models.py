"""全部 ORM 模型。

字段命名与前端契约（PanKeeper-vue3/docs/api-contract.md、src/types/model.ts）对齐：
对外 JSON 直接用模型转字典，避免两套命名。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now_str() -> str:
    return datetime.now().strftime("%m-%d %H:%M")


class Account(Base):
    """网盘账号（每平台可配置多个，同时在线）。

    旧表 accounts（type 主键，单账号）已由迁移搬入本表并原样保留（回滚保险）。
    cookies_enc 为 Fernet 加密密文，任何接口不回明文。
    """

    __tablename__ = "drive_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text)  # baidu|quark|115
    alias: Mapped[str] = mapped_column(Text, default="")  # 显示别名；空则用昵称
    cookies_enc: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="unset")  # connected|expired|unset
    nickname: Mapped[str] = mapped_column(Text, default="")
    last_check: Mapped[str] = mapped_column(Text, default="从未配置")

    @property
    def display_name(self) -> str:
        """切换下拉/通知里展示的名字：别名 > 昵称 > 平台名+序号。"""
        return self.alias or self.nickname or f"{self.type}#{self.id}"


class RequestStat(Base):
    """按「日期 + 网盘」累计的请求次数（网盘日志页展示 + 风控预警用）。

    只存计数，不存请求明细——明细量级太大，且页面要的是「今天发了多少」。
    """

    __tablename__ = "request_stat"

    date: Mapped[str] = mapped_column(Text, primary_key=True)  # YYYY-MM-DD
    drive: Mapped[str] = mapped_column(Text, primary_key=True)  # baidu|quark|115
    count: Mapped[int] = mapped_column(Integer, default=0)


class PaTask(Base):
    """自动转存任务（含任务弹窗的扩展字段，全部并表，不留内存 map）。"""
    __tablename__ = "pa_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, default="baidu")
    acc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 用哪个账号跑；空=该类型第一个
    name: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    share_url: Mapped[str] = mapped_column(Text, default="")
    share_code: Mapped[str] = mapped_column(Text, default="")
    save_dir: Mapped[str] = mapped_column(Text, default="")
    compare_path: Mapped[str] = mapped_column(Text, default="")
    include_subdirs: Mapped[bool] = mapped_column(Boolean, default=True)
    cron: Mapped[str] = mapped_column(Text, default="")  # 空 = 仅手动
    exclude_json: Mapped[str] = mapped_column(Text, default="[]")  # 排除清单（文件名列表）
    exclude_md5_json: Mapped[str] = mapped_column(Text, default="[]")  # 排除清单（MD5 列表，与文件名任一命中即排除）
    exclude_count: Mapped[int] = mapped_column(Integer, default=0)
    qms_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regex_pattern: Mapped[str] = mapped_column(Text, default="")
    regex_replace: Mapped[str] = mapped_column(Text, default="")
    drill_on: Mapped[bool] = mapped_column(Boolean, default=False)
    drill_json: Mapped[str] = mapped_column(Text, default="[]")  # 下钻勾选：字段保留、配置不丢；功能短期不实现（2026-10-03 定）
    post_qms: Mapped[bool] = mapped_column(Boolean, default=False)
    post_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[str] = mapped_column(Text, default="")
    last_status: Mapped[str] = mapped_column(Text, default="never")  # success|fail|running|never
    last_result: Mapped[str] = mapped_column(Text, default="—")
    ban_reason: Mapped[str] = mapped_column(Text, default="")  # 分享失效熔断标记


class DdItem(Base):
    """转存配置目录（快速转存的依据），is_default 每账号唯一。"""

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


class QmsPath(Base):
    __tablename__ = "qms_paths"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text)  # tv|movie
    source_path: Mapped[str] = mapped_column(Text)


class StrmPath(Base):
    __tablename__ = "strm_paths"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_path: Mapped[str] = mapped_column(Text)


class QueueTaskRow(Base):
    """队列任务快照：内存是权威，这里负责跨重启恢复。"""

    __tablename__ = "queue_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, default="/")
    files: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[str] = mapped_column(Text, default="—")
    status: Mapped[str] = mapped_column(Text, default="wait")  # wait|run|done|fail
    phase: Mapped[str] = mapped_column(Text, default="")
    phase_start: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    done_at: Mapped[int] = mapped_column(Integer, default=0)
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    # 转存执行需要的源信息（mock 时代没有，真实转存必须有）
    share_url: Mapped[str] = mapped_column(Text, default="")
    share_code: Mapped[str] = mapped_column(Text, default="")
    include_subdirs: Mapped[bool] = mapped_column(Boolean, default=True)
    # 自动任务链路（M3）：调度器入队时带上，完成后回写 PaTask 状态 + RunHistory
    acc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 指定账号；空=该类型默认
    pa_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 来源 PaTask（自动任务才有）
    exclude_json: Mapped[str] = mapped_column(Text, default="[]")  # 排除清单（文件名列表）
    exclude_md5_json: Mapped[str] = mapped_column(Text, default="[]")  # 排除清单（MD5，与文件名任一命中即排除）
    regex_pattern: Mapped[str] = mapped_column(Text, default="")  # 任务正则（引擎透传 run_auto；此前漏存致正则失效）


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")


class Record(Base):
    """转存记录快照：只追加，事后改配置不影响旧记录。"""

    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    n: Mapped[str] = mapped_column(Text)  # 资源名
    t: Mapped[str] = mapped_column(Text)  # 网盘类型
    p: Mapped[str] = mapped_column(Text)  # 目标路径
    st: Mapped[str] = mapped_column(Text)  # 结果文案（完成 36/36）
    cls: Mapped[str] = mapped_column(Text, default="t-ok")  # t-ok|t-bad|t-off
    tm: Mapped[str] = mapped_column(Text)  # MM-DD HH:mm
    qms_json: Mapped[str] = mapped_column(Text, default='{"st":"未执行","cls":"t-off"}')
    strm_json: Mapped[str] = mapped_column(Text, default='{"st":"未执行","cls":"t-off"}')
    share_url: Mapped[str] = mapped_column(Text, default="")
    share_code: Mapped[str] = mapped_column(Text, default="")
    # 记录来源：search=手动查询转存（记录页展示）/ auto=自动转存（转存历史页 + 任务内转存日志）
    source: Mapped[str] = mapped_column(Text, default="search")
    cron: Mapped[str] = mapped_column(Text, default="")  # 空 = 手动转存（不接推送）
    include_subdirs: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_count: Mapped[int] = mapped_column(Integer, default=0)
    post_qms: Mapped[bool] = mapped_column(Boolean, default=False)
    post_notify: Mapped[bool] = mapped_column(Boolean, default=False)
    logs_json: Mapped[str] = mapped_column(Text, default="[]")  # 详情抽屉的执行日志
    files_json: Mapped[str] = mapped_column(Text, default="[]")  # 分享内文件清单快照 [{path,name,size,st}]


class RunHistory(Base):
    """自动任务执行历史（保留条数在写入时裁剪）。"""

    __tablename__ = "run_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    started: Mapped[str] = mapped_column(Text)
    finished: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="running")
    add: Mapped[int] = mapped_column(Integer, default=0)
    skip: Mapped[int] = mapped_column(Integer, default=0)
    skip_md5: Mapped[int] = mapped_column(Integer, default=0)  # 跳过里 MD5 命中的部分
    fail: Mapped[int] = mapped_column(Integer, default=0)
    excl: Mapped[int] = mapped_column(Integer, default=0)
    total_share: Mapped[int] = mapped_column(Integer, default=0)  # 分享内文件总数
    regex_miss: Mapped[int] = mapped_column(Integer, default=0)  # 正则未命中（被过滤）的文件数
    message: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[int] = mapped_column(Integer, default=0)  # 耗时（秒）
    transferred_json: Mapped[str] = mapped_column(Text, default="[]")  # 本次实际转存的文件名
    excluded_json: Mapped[str] = mapped_column(Text, default="[]")  # 排除清单跳过的文件名
    regex_hit_json: Mapped[str] = mapped_column(Text, default="[]")  # 正则命中（过滤后放行）的文件名
    # MD5 去重命中的文件名（只记录、不回写任务排除清单——2026-10-04 取消回写，但要让用户能查到"哪集被滤掉"）
    md5_skipped_json: Mapped[str] = mapped_column(Text, default="[]")
    # QMS/STRM 联动结果快照（{"st": 展示文案, "cls": "t-ok"/"t-bad"/"t-off"}）；
    # 旧记录为空串，前端对空值显示「—」。详情弹窗「执行结果」行用。
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
    """目录 ID → 路径 的稳定映射（无 TTL，rename/move/delete 时级联删）。"""

    __tablename__ = "dir_path_cache"
    account_type: Mapped[str] = mapped_column(Text, primary_key=True)
    dir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dir_path: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[str] = mapped_column(Text, default="")
    last_seen_at: Mapped[int] = mapped_column(Integer, default=0)


class DirTreeCacheRow(Base):
    """目录树缓存持久层：内存 LRU 为主，写穿到这里，重启后免回源恢复。

    键 = (type, acc, cid)；过期判断靠 expires_at（恢复时丢掉过期行）。
    """

    __tablename__ = "dir_tree_cache"
    type: Mapped[str] = mapped_column(Text, primary_key=True)
    acc: Mapped[str] = mapped_column(Text, primary_key=True)
    cid: Mapped[str] = mapped_column(Text, primary_key=True)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[int] = mapped_column(Integer, default=0)
    cached_at: Mapped[int] = mapped_column(Integer, default=0)


class ShareListCacheRow(Base):
    """分享文件清单缓存持久层（与目录缓存不同逻辑：转存驱动刷新，无 TTL）。

    键 = type + 分享链接(+提取码)；转存跑完就刷新一次，查看/排除弹窗在两次转存之间命中。
    """

    __tablename__ = "share_list_cache"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    cached_at: Mapped[int] = mapped_column(Integer, default=0)


class PushLog(Base):
    """推送历史（Server 酱/Webhook 每次投递一行快照）。

    只记真正发出的推送（被时机开关拦掉的不记）；推送失败不阻塞主流程，这里补账。"""

    __tablename__ = "push_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text)  # "YYYY-MM-DD HH:MM:SS"
    title: Mapped[str] = mapped_column(Text, default="")  # 推送标题（即推送内容摘要）
    kind: Mapped[str] = mapped_column(Text, default="info")  # notify.push 的 kind
    status: Mapped[str] = mapped_column(Text, default="success")  # success|fail
    error: Mapped[str] = mapped_column(Text, default="")  # 失败原因（多渠道用「；」拼接）
