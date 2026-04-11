from __future__ import annotations

from pathlib import Path

from echofinder.models.file_node import FileNode, FileType
from echofinder.services.file_type import FileTypeResolver


def scan_directory(
    node: FileNode,
    root: Path | None,
    resolver: FileTypeResolver,
) -> list[FileNode]:
    """Return sorted child FileNodes for *node*, or [] on permission error.

    Directories come before files/symlinks; both groups sorted case-insensitively.
    Children are not scanned recursively — callers load deeper levels on demand.
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
        children.append(FileNode(path=entry, file_type=file_type, parent=node, row=i))

    return children
