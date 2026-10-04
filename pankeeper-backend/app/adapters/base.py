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
    # 分享内完整路径（/目录/子目录/文件名），文件树展示用
    path: str = "" 
    # 目标名（正则/重命名后），None 表示沿用原名
    target_name: str | None = None
    # 分享侧文件 MD5（百度直接返回；MD5 优先去重的依据，夸克没有就留空）
    md5: str = ""


@dataclass
class TransferResult:
    add: int = 0
    skip: int = 0
    skip_md5: int = 0  # 其中 MD5 命中跳过数（按名字跳过 = skip - skip_md5）
    fail: int = 0
    transferred: list[dict] = field(default_factory=list)  # [{name, fid}]
    renamed: int = 0
    # MD5 命中被跳过的文件（[{name, md5}]）——自动任务把它回写进任务排除清单（双保险 + 排除弹窗可见）
    md5_skipped: list[dict] = field(default_factory=list)


@dataclass
class TaskSpec:
    """一次转存任务的输入（队列引擎组装）。"""

    share_url: str
    share_code: str = ""
    save_dir: str = "/"
    include_subdirs: bool = True
    rename_map: dict[str, str] = field(default_factory=dict)  # 原名 → 目标名
    exclude_names: set[str] = field(default_factory=set)
    # 排除清单（MD5）：与文件名任一命中即排除（分享内改名的文件靠 MD5 兜住）
    exclude_md5s: set[str] = field(default_factory=set)
    # MD5 对比基线目录（自动任务的去重对比路径；空=用 save_dir）
    compare_path: str = ""
    # 只转存分享内这些相对路径（勾选清单；None/空 = 全部）。
    # 目录条目也按路径匹配：勾了目录 = 转该目录整棵子树（bdsavePro new_files 语义）
    only_paths: set[str] | None = None
    # 「建壳转存」（搜索转存快速弹窗专用，2026-10-04 用户定稿）：在 save_dir 下按
    # 资源名（或 folder_rename）**新建**一个文件夹当壳，分享内容剥壳转进去——
    # 不再"整壳转 + rename_dir 事后改名"：搜索源给的名字是资源名而非分享真实目录名，
    # 整壳转过来名字对不上；事后改名实测会被可见性延迟/风控拽失败。整壳转还有个
    # 连带坑：transferred 里只有壳一条，run_watch 拿壳名查 QMS 逐文件记录永远查不到。
    with_shell: bool = False
    # 壳文件夹名覆盖：非空时新建的壳用它命名（不再事后 rename_dir）
    folder_rename: str = ""
    # 资源名/任务名（队列任务名 = 更名值或资源名）：建壳时没填更名就用它当壳名
    share_name: str = ""


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
