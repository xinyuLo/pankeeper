from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts",
    ".iso",
}

def is_video_file(name: str) -> bool:
    dot = name.rfind(".")
    if dot < 0:
        return False
    return name[dot:].lower() in VIDEO_EXTS

def split_video_files(files: list["ShareFile"]) -> tuple[list["ShareFile"], list[str]]:
    kept: list[ShareFile] = []
    dropped: list[str] = []
    for f in files:
        if f.is_dir or is_video_file(f.name):
            kept.append(f)
        else:
            dropped.append(f.name)
    return kept, dropped

class ShareBanned(Exception):
    pass

class CredentialExpired(Exception):
    pass

class AdapterError(Exception):
    pass

class RiskControlError(AdapterError):
    pass

@dataclass
class ShareFile:

    fid: str
    fid_token: str = ""
    name: str = ""
    is_dir: bool = False
    size: int = 0

    path: str = "" 

    target_name: str | None = None

    md5: str = ""

@dataclass
class TransferResult:
    add: int = 0
    skip: int = 0
    skip_md5: int = 0
    fail: int = 0
    transferred: list[dict] = field(default_factory=list)
    renamed: int = 0

    md5_skipped: list[dict] = field(default_factory=list)

@dataclass
class TaskSpec:

    share_url: str
    share_code: str = ""
    save_dir: str = "/"
    include_subdirs: bool = True
    rename_map: dict[str, str] = field(default_factory=dict)
    exclude_names: set[str] = field(default_factory=set)

    exclude_md5s: set[str] = field(default_factory=set)

    compare_path: str = ""

    only_paths: set[str] | None = None

    with_shell: bool = False

    folder_rename: str = ""

    share_name: str = ""

class CloudAdapter(ABC):

    type: str = ""

    @abstractmethod
    def verify(self) -> str:
        pass

    @abstractmethod
    def list_share(self, spec: TaskSpec) -> list[ShareFile]:
        pass

    def probe_share(self, spec: TaskSpec) -> list[ShareFile] | None:
        return None

    @abstractmethod
    def list_dir_names(self, dir_path: str) -> set[str]:
        pass

    @abstractmethod
    def save_files(
        self,
        files: list[ShareFile],
        spec: TaskSpec,
        on_progress,
        on_log,
    ) -> TransferResult:
        pass
