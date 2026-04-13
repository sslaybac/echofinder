from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


class FileType(Enum):
    """Classification of a filesystem entry for display and preview routing.

    Values are used as the primary dispatch key in ``PreviewPane.show_for_node``
    and in the slot-1 icon lookup in ``icons.icon_for_type``.
    """

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
    """A single node in the file tree, representing one filesystem entry.

    Instances are created by ``scanner.scan_directory`` and stored as
    ``children`` lists on their parent nodes.  The tree model holds a flat
    ``_path_to_node`` index for O(1) lookups by path string.

    Attributes:
        path: Absolute ``Path`` to this entry.
        file_type: Classification used for slot-1 icon and preview routing.
        parent: Parent ``FileNode``, or ``None`` for the root.
        row: Zero-based index of this node within its parent's ``children``.
        ownership: Slot-2 indicator: how the current user owns this entry.
        permission: Slot-3 indicator: the current user's read/write access.
        hash_state: Slot-4 indicator: hashing / duplicate status.
        prior_hash_state: Saved slot-4 state restored when rename mode exits.
        children: Loaded child nodes, or ``None`` if not yet fetched.
    """

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
        """Display name: the final path component, or the full path for the root.

        Returns:
            A non-empty string suitable for display in the tree view.
        """
        return self.path.name if self.path.name else str(self.path)

    @property
    def is_dir(self) -> bool:
        """Return ``True`` if this node represents a directory.

        Returns:
            ``True`` for ``FileType.FOLDER`` nodes.
        """
        return self.file_type == FileType.FOLDER

    @property
    def is_symlink(self) -> bool:
        """Return ``True`` if this node represents a symbolic link.

        Returns:
            ``True`` for ``SYMLINK_INTERNAL`` and ``SYMLINK_EXTERNAL`` nodes.
        """
        return self.file_type in (FileType.SYMLINK_INTERNAL, FileType.SYMLINK_EXTERNAL)

    @property
    def children_loaded(self) -> bool:
        """Return ``True`` if ``fetchMore`` has already populated ``children``.

        Returns:
            ``True`` when ``children`` is a list (even if empty).
        """
        return self.children is not None
