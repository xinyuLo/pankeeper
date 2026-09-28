"""网盘适配器抽象：队列引擎只面向这组接口，不关心具体网盘。

实现依据说明：各网盘的 API 端点/参数均为公开接口事实（调研见 docs/research/），
本仓库全部代码为原创实现，不含任何参考项目的代码与注释。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ShareBanned(Exception):
    """分享失效/被取消：调用方应熔断标记，不再对死链接发请求。"""


class CredentialExpired(Exception):
    """Cookie 失效：账号标记 expired 并告警。"""


class AdapterError(Exception):
    """一般转存失败。"""


@dataclass
class ShareFile:
    """分享内文件条目。"""

    fid: str
    fid_token: str = ""
    name: str = ""
    is_dir: bool = False
    size: int = 0
    # 目标名（正则/重命名后），None 表示沿用原名
    target_name: str | None = None


@dataclass
class TransferResult:
    add: int = 0
    skip: int = 0
    fail: int = 0
    transferred: list[dict] = field(default_factory=list)  # [{name, fid}]
    renamed: int = 0


@dataclass
class TaskSpec:
    """一次转存任务的输入（队列引擎组装）。"""

    share_url: str
    share_code: str = ""
    save_dir: str = "/"
    include_subdirs: bool = True
    rename_map: dict[str, str] = field(default_factory=dict)  # 原名 → 目标名
    exclude_names: set[str] = field(default_factory=set)


class CloudAdapter(ABC):
    """一个实例对应一个账号（持有一份 Cookie）。"""

    type: str = ""

    @abstractmethod
    def verify(self) -> str:
        """校验凭据，返回昵称；失效抛 CredentialExpired。"""

    @abstractmethod
    def list_share(self, spec: TaskSpec) -> list[ShareFile]:
        """解析分享并列出文件清单（已按 include_subdirs / 排除清单 / 自动下钻处理）。"""

    @abstractmethod
    def list_dir_names(self, dir_path: str) -> set[str]:
        """目标目录现有文件名集合（去重基线）。目录不存在返回空集。"""

    @abstractmethod
    def save_files(
        self,
        files: list[ShareFile],
        spec: TaskSpec,
        on_progress,
        on_log,
    ) -> TransferResult:
        """执行转存（含建目录/分批/轮询/重命名/去重跳过）。on_progress(0-100)。"""
