"""网盘适配器抽象：队列引擎只面向这组接口，不关心具体网盘。

实现依据说明：各网盘的 API 端点/参数均为公开接口事实（调研见 docs/research/），
本仓库全部代码为原创实现，不含任何参考项目的代码与注释。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# 视频扩展名白名单：目录「过滤其他文件」开关与 QMS 等待/未见统计共用这一份口径
# （QMS 只整理视频；nfo/海报图片/字幕等杂件会被它过滤、永不建刮削记录——
# 2026-10-07 实锤：杂件被算进等待清单 → 推送干等 30 分钟超时，Server酱静默）
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts",
    ".iso",  # 蓝光/DVD 原盘镜像（用户点名：iso 也是视频）
}


def is_video_file(name: str) -> bool:
    """按扩展名判断是否视频文件（无扩展名一律不算）。"""
    dot = name.rfind(".")
    if dot < 0:
        return False
    return name[dot:].lower() in VIDEO_EXTS


def split_video_files(files: list["ShareFile"]) -> tuple[list["ShareFile"], list[str]]:
    """把分享清单拆成 (保留, 剔除文件名)。目录条目永远保留——子目录里的视频还要靠它进入。"""
    kept: list[ShareFile] = []
    dropped: list[str] = []
    for f in files:
        if f.is_dir or is_video_file(f.name):
            kept.append(f)
        else:
            dropped.append(f.name)
    return kept, dropped


class ShareBanned(Exception):
    """分享失效/被取消：调用方应熔断标记，不再对死链接发请求。"""


class CredentialExpired(Exception):
    """Cookie 失效：账号标记 expired 并告警。"""


class AdapterError(Exception):
    """一般转存失败。"""


class RiskControlError(AdapterError):
    """网盘风控/限频（115 的 302 跳风控页、406 限频、验证码告警）。

    这是账号级临时封禁：任何自动重试都是拿账号往枪口上撞（越打封越久，
    2026-10-07 用户实锤目录浏览触发风控）。后端据此回 HTTP 429，前端见到
    429 一次都不重打。"""


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

    def probe_share(self, spec: TaskSpec) -> list[ShareFile] | None:
        """死活预检的轻量探针：只拉**第一页小分页**，非空即有效（判死活不需要整个根层）。

        默认不实现（返回 None → 调用方回落 list_share）；115/夸克各实现一份——
        它们的根层翻页是大头：夸克每页 50 条、500 文件根层要 10 连发，115 一次
        limit=1000 的大 JSON（2026-10-08 用户实锤"检测有效链太慢"）。"""
        return None

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
