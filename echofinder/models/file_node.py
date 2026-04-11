from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


class FileType(Enum):
    FOLDER = auto()
    IMAGE = auto()
    VIDEO = auto()
    AUDIO = auto()
    PDF = auto()
    TEXT = auto()
    CODE = auto()
    SYMLINK_INTERNAL = auto()
    SYMLINK_EXTERNAL = auto()
    UNKNOWN = auto()


@dataclass
class FileNode:
    path: Path
    file_type: FileType
    parent: FileNode | None = field(default=None, repr=False, compare=False)
    row: int = 0  # index within parent.children
    children: list[FileNode] | None = field(default=None, repr=False)  # None = not yet scanned

    @property
    def name(self) -> str:
        return self.path.name if self.path.name else str(self.path)

    @property
    def is_dir(self) -> bool:
        return self.file_type == FileType.FOLDER

    @property
    def is_symlink(self) -> bool:
        return self.file_type in (FileType.SYMLINK_INTERNAL, FileType.SYMLINK_EXTERNAL)

    @property
    def children_loaded(self) -> bool:
        return self.children is not None
