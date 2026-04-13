"""Business logic for file deletion and movement.

No Qt widget imports. All Qt interaction is mediated via the callback
parameters passed by the UI layer.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional


ConflictAction = Literal["overwrite", "skip"]


@dataclass
class DeleteResult:
    """Result of a ``delete_item`` operation.

    Attributes:
        success: ``True`` if the item was successfully trashed.
        error_msg: Human-readable failure description, or ``None`` on success.
    """

    success: bool
    error_msg: Optional[str] = None


@dataclass
class MoveResult:
    """Result of a ``move_item`` or ``_do_merge`` operation.

    Attributes:
        success: ``True`` if the overall operation completed without a fatal
            error.  A successful merge may still have per-file ``failures``.
        failures: List of ``(path, reason)`` pairs for files that could not be
            moved during a directory merge.
        error_msg: Human-readable description of a fatal error, or ``None``.
    """

    success: bool
    failures: list[tuple[str, str]] = field(default_factory=list)
    error_msg: Optional[str] = None


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

@dataclass
class RenameResult:
    """Result of a ``rename_item`` operation.

    Attributes:
        success: ``True`` if the rename completed without error.
        error_msg: Human-readable failure description, or ``None`` on success.
    """

    success: bool
    error_msg: Optional[str] = None


def rename_item(old_path: Path, new_path: Path) -> RenameResult:
    """Rename *old_path* to *new_path* using os.rename()."""
    try:
        os.rename(str(old_path), str(new_path))
        return RenameResult(success=True)
    except PermissionError:
        return RenameResult(
            success=False,
            error_msg=f"Permission denied: cannot rename \u2018{old_path.name}\u2019.",
        )
    except OSError:
        return RenameResult(
            success=False,
            error_msg=f"Could not rename \u2018{old_path.name}\u2019.",
        )


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def delete_item(path: Path) -> DeleteResult:
    """Send *path* to the system trash via send2trash."""
    try:
        from send2trash import send2trash  # type: ignore[import]
        send2trash(str(path))
        return DeleteResult(success=True)
    except PermissionError:
        return DeleteResult(
            success=False,
            error_msg=f"Permission denied: cannot delete \u2018{path.name}\u2019.",
        )
    except OSError:
        return DeleteResult(
            success=False,
            error_msg=(
                f"Could not delete \u2018{path.name}\u2019. "
                "The item may be in use or inaccessible."
            ),
        )
    except Exception:
        return DeleteResult(
            success=False,
            error_msg=f"Could not delete \u2018{path.name}\u2019.",
        )


# ---------------------------------------------------------------------------
# Movement helpers
# ---------------------------------------------------------------------------

def compute_total_size(path: Path) -> int:
    """Recursively sum file sizes under *path* (worst-case upper bound)."""
    total = 0
    if path.is_symlink() or path.is_file():
        try:
            total += path.stat().st_size
        except OSError:
            pass
        return total
    try:
        for dirpath, _dirs, files in os.walk(
            str(path), followlinks=False, onerror=lambda _: None
        ):
            for fname in files:
                try:
                    total += os.stat(os.path.join(dirpath, fname)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}\u202f{unit}"
        size_bytes /= 1024.0  # type: ignore[assignment]
    return f"{size_bytes:.1f}\u202fPB"


def check_name_collision(parent_dir: Path, name: str) -> bool:
    """Return True if *parent_dir* already contains an entry named *name*."""
    try:
        return (parent_dir / name).exists()
    except OSError:
        return False


def is_cross_filesystem(src: Path, dst_parent: Path) -> bool:
    """Return True when src and dst_parent reside on different filesystems."""
    try:
        return os.stat(str(src)).st_dev != os.stat(str(dst_parent)).st_dev
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Movement — main entry point
# ---------------------------------------------------------------------------

def move_item(
    src: Path,
    dst_parent: Path,
    *,
    confirm_cross_fs: Callable[[str], bool],
    confirm_merge: Callable[[str, str], bool],
    resolve_conflict: Callable[[str], tuple[ConflictAction, bool]],
    cache_update: Callable[[str, str], None],
    tree_refresh: Callable[[Path, Path], None],
) -> MoveResult:
    """Move *src* into *dst_parent*.

    Callback contracts
    ------------------
    confirm_cross_fs(size_str) -> bool
        Show a modal warning about copy-then-delete; return True to proceed.
    confirm_merge(src_name, dst_path_str) -> bool
        Warn the user about a directory merge; return True to proceed.
    resolve_conflict(filename) -> (action, apply_to_all)
        Per-file collision dialog; action is 'overwrite' or 'skip'.
    cache_update(old_path_str, new_path_str)
        Update the hash-cache entry after a successful file move.
    tree_refresh(old_path, new_path)
        Signal the UI to refresh affected tree nodes.
    """
    dst = dst_parent / src.name

    # Guard: cannot move a folder into itself or a descendant.
    try:
        dst.relative_to(src)
        return MoveResult(
            success=False,
            error_msg="Cannot move a folder into itself or one of its subfolders.",
        )
    except ValueError:
        pass

    # Guard: source already equals destination (no-op).
    try:
        if src.resolve() == dst.resolve():
            return MoveResult(success=True)
    except OSError:
        pass

    cross_fs = is_cross_filesystem(src, dst_parent)

    if cross_fs:
        size_str = format_size(compute_total_size(src))
        if not confirm_cross_fs(size_str):
            return MoveResult(success=False)

    # Directory merge case
    if dst.is_dir() and src.is_dir():
        if not confirm_merge(src.name, str(dst)):
            return MoveResult(success=False)
        return _do_merge(src, dst, cross_fs, resolve_conflict, cache_update, tree_refresh)

    # Simple move (file or non-colliding directory)
    if dst.exists():
        action, _ = resolve_conflict(src.name)
        if action == "skip":
            return MoveResult(success=False)
        # Overwrite: remove existing destination first
        try:
            if dst.is_dir():
                shutil.rmtree(str(dst))
            else:
                dst.unlink()
        except PermissionError:
            return MoveResult(
                success=False,
                error_msg=f"Permission denied overwriting \u2018{dst.name}\u2019.",
            )
        except OSError:
            return MoveResult(
                success=False,
                error_msg=f"Could not overwrite \u2018{dst.name}\u2019.",
            )

    if cross_fs:
        try:
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
        except PermissionError:
            return MoveResult(
                success=False,
                error_msg=f"Permission denied copying \u2018{src.name}\u2019.",
            )
        except OSError:
            return MoveResult(
                success=False,
                error_msg=f"Could not copy \u2018{src.name}\u2019.",
            )
        # Best-effort cleanup of source after copy
        try:
            if src.is_dir():
                shutil.rmtree(str(src))
            else:
                src.unlink()
        except (PermissionError, OSError):
            pass
    else:
        try:
            os.rename(str(src), str(dst))
        except PermissionError:
            return MoveResult(
                success=False,
                error_msg=f"Permission denied: cannot move \u2018{src.name}\u2019.",
            )
        except OSError:
            return MoveResult(
                success=False,
                error_msg=f"Could not move \u2018{src.name}\u2019.",
            )

    cache_update(str(src), str(dst))
    tree_refresh(src, dst)
    return MoveResult(success=True)


# ---------------------------------------------------------------------------
# Directory merge
# ---------------------------------------------------------------------------

def _do_merge(
    src_dir: Path,
    dst_dir: Path,
    cross_fs: bool,
    resolve_conflict: Callable[[str], tuple[ConflictAction, bool]],
    cache_update: Callable[[str, str], None],
    tree_refresh: Callable[[Path, Path], None],
) -> MoveResult:
    """Recursively merge *src_dir* into *dst_dir* (which already exists)."""
    failures: list[tuple[str, str]] = []
    # 'apply_to_all' is set after user chooses "apply to all" in conflict dialog.
    apply_to_all: Optional[ConflictAction] = None

    for dirpath_str, subdirs, files in os.walk(
        str(src_dir), followlinks=False, onerror=lambda _: None
    ):
        src_root = Path(dirpath_str)
        rel = src_root.relative_to(src_dir)
        dst_root = dst_dir / rel

        if not dst_root.exists():
            try:
                dst_root.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError):
                failures.append((str(src_root), "Could not create destination directory."))
                subdirs.clear()  # prune this branch of the walk
                continue

        for fname in files:
            src_file = src_root / fname
            dst_file = dst_root / fname

            if dst_file.exists():
                if apply_to_all is not None:
                    action: ConflictAction = apply_to_all
                else:
                    try:
                        action, use_all = resolve_conflict(fname)
                        if use_all:
                            apply_to_all = action
                    except Exception:
                        action = "skip"

                if action == "skip":
                    continue
                # overwrite: dst_file will be replaced by rename/copy below

            try:
                if cross_fs:
                    shutil.copy2(str(src_file), str(dst_file))
                    try:
                        src_file.unlink()
                    except (PermissionError, OSError):
                        failures.append(
                            (str(src_file), "Copied but could not remove original.")
                        )
                        continue
                else:
                    os.rename(str(src_file), str(dst_file))
                cache_update(str(src_file), str(dst_file))
            except PermissionError:
                failures.append((str(src_file), "Permission denied."))
            except OSError:
                failures.append((str(src_file), "Could not move file."))

    # Remove whatever remains of the source directory tree
    try:
        shutil.rmtree(str(src_dir), ignore_errors=True)
    except OSError:
        pass

    tree_refresh(src_dir, dst_dir)
    return MoveResult(success=True, failures=failures)
