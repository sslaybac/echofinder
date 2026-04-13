from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

from echofinder.models.file_node import (
    FileNode,
    FileType,
    HashState,
    OwnershipState,
    PermissionState,
)
from echofinder.services.file_type import FileTypeResolver

# Cache current user's UID and supplemental GIDs at import time.
# These are stable for the lifetime of the process on any normal Unix system.
# os.getuid() / os.getgroups() are POSIX-only; on Windows ownership indicators
# are not shown (all files resolve to OwnershipState.NEITHER).
if sys.platform != "win32":
    _CURRENT_UID: int = os.getuid()
    _CURRENT_GIDS: frozenset[int] = frozenset(os.getgroups())
else:
    _CURRENT_UID = -1
    _CURRENT_GIDS: frozenset[int] = frozenset()


def walk_files(root: Path) -> Iterator[Path]:
    """Yield every non-directory path under *root*, without following symlinks.

    Errors accessing subdirectories are silently ignored.  Symlinks to files
    are included; the caller is responsible for deciding whether to hash them.
    """
    for dirpath, _dirnames, filenames in os.walk(
        str(root), followlinks=False, onerror=lambda _: None
    ):
        dp = Path(dirpath)
        for name in filenames:
            yield dp / name


def _evaluate_ownership(stat_result: os.stat_result) -> OwnershipState:
    """Determine ownership state using priority order from Section 4.1."""
    if stat_result.st_uid == _CURRENT_UID:
        return OwnershipState.INDIVIDUAL
    if stat_result.st_gid in _CURRENT_GIDS:
        return OwnershipState.GROUP
    return OwnershipState.NEITHER


def _evaluate_permission(path: Path) -> PermissionState:
    """Determine permission state.

    Write-only files are collapsed into NOT_READABLE per the design decision:
    the application's primary operations all require read access, so write-only
    behaves identically to fully inaccessible from the application's perspective.
    """
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    if readable and writable:
        return PermissionState.READ_WRITE
    if readable:
        return PermissionState.READ_ONLY
    return PermissionState.NOT_READABLE


def _initial_hash_state(file_type: FileType, permission: PermissionState) -> HashState:
    """Determine the initial slot-4 state for a newly created node.

    Enforces mutual exclusivity rules:
    - Dirs and symlinks never enter any hash state (slot 4 always empty → UNIQUE).
    - Unreadable files are never hashed and must not show the hourglass.
    - Readable, non-dir, non-symlink files start as NOT_HASHED (hourglass).
    """
    if file_type == FileType.FOLDER:
        return HashState.UNIQUE
    if file_type in (FileType.SYMLINK_INTERNAL, FileType.SYMLINK_EXTERNAL):
        return HashState.UNIQUE
    if permission == PermissionState.NOT_READABLE:
        return HashState.UNIQUE  # mutual exclusivity: no hourglass for unreadable files
    return HashState.NOT_HASHED


def scan_directory(
    node: FileNode,
    root: Path | None,
    resolver: FileTypeResolver,
) -> list[FileNode]:
    """Return sorted child FileNodes for *node*, or [] on permission error.

    Directories come before files/symlinks; both groups sorted case-insensitively.
    Children are not scanned recursively — callers load deeper levels on demand.

    Ownership and permission for each child are evaluated here so that slot-2
    and slot-3 indicators are available immediately when the node is displayed.
    """
    try:
        entries = sorted(
            node.path.iterdir(),
            key=lambda e: (e.is_symlink() or not e.is_dir(), e.name.lower()),
        )
    except (PermissionError, OSError):
        return []

    children: list[FileNode] = []
    for i, entry in enumerate(entries):
        try:
            file_type = resolver.resolve(entry, root)
        except Exception:
            file_type = FileType.UNKNOWN

        # Evaluate ownership (requires stat)
        ownership = OwnershipState.NEITHER  # safe fallback
        try:
            stat_result = entry.stat(follow_symlinks=False)
            ownership = _evaluate_ownership(stat_result)
        except OSError:
            pass

        # Evaluate permissions (uses os.access)
        permission = PermissionState.NOT_READABLE  # safe fallback
        try:
            permission = _evaluate_permission(entry)
        except OSError:
            pass

        hash_state = _initial_hash_state(file_type, permission)

        children.append(
            FileNode(
                path=entry,
                file_type=file_type,
                parent=node,
                row=i,
                ownership=ownership,
                permission=permission,
                hash_state=hash_state,
            )
        )

    return children
