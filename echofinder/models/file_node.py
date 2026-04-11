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


class OwnershipState(Enum):
    """Slot 2: who owns this filesystem entry relative to the current user."""
    INDIVIDUAL = auto()  # current user is the individual (UID) owner
    GROUP = auto()       # current user belongs to the owning group (not individual owner)
    NEITHER = auto()     # current user is neither individual owner nor in owning group


class PermissionState(Enum):
    """Slot 3: current user's effective read/write access to this entry."""
    READ_WRITE = auto()   # readable and writable
    READ_ONLY = auto()    # readable but not writable
    NOT_READABLE = auto() # not readable (includes write-only; padlock shown)


class HashState(Enum):
    """Slot 4: hashing/duplicate status.

    States are mutually exclusive.  The RENAME_CONFLICT state is a temporary
    override during rename operations (wired in Stage 6); the model field
    ``prior_hash_state`` stores the state to restore when rename mode exits.
    """
    NOT_HASHED = auto()        # queued but not yet hashed; hourglass shown (muted)
    UNIQUE = auto()            # hashed; no duplicates found; no slot-4 icon
    DUPLICATE_GENERAL = auto() # hashed; has duplicates; copy icon shown
    DUPLICATE_SPECIFIC = auto()# hashed; is a duplicate of the file open in the main pane
    RENAME_CONFLICT = auto()   # rename-in-progress override; warning icon (Stage 6)


@dataclass
class FileNode:
    path: Path
    file_type: FileType
    parent: FileNode | None = field(default=None, repr=False, compare=False)
    row: int = 0  # index within parent.children

    # Slot indicators (computed at scan time; hash_state updated as hashing progresses)
    ownership: OwnershipState = field(default=OwnershipState.NEITHER, compare=False)
    permission: PermissionState = field(default=PermissionState.READ_WRITE, compare=False)
    hash_state: HashState = field(default=HashState.NOT_HASHED, compare=False)
    # Saved state for rename-conflict restore (Stage 6 writes/reads this)
    prior_hash_state: HashState = field(default=HashState.NOT_HASHED, compare=False)

    children: list[FileNode] | None = field(default=None, repr=False, compare=False)

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
