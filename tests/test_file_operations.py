"""Tests for file_operations service — no Qt, no UI.

Run with:  uv run pytest tests/test_file_operations.py -v
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from echofinder.services.file_operations import (
    MoveResult,
    RenameResult,
    DeleteResult,
    check_name_collision,
    compute_total_size,
    delete_item,
    format_size,
    is_cross_filesystem,
    move_item,
    rename_item,
)


# ---------------------------------------------------------------------------
# Callback factories
# ---------------------------------------------------------------------------

def _noop_cache_update(old: str, new: str) -> None:
    pass


def _noop_tree_refresh(old: Path, new: Path) -> None:
    pass


def _recording_cache_update(log: list[tuple[str, str]]):
    def _cb(old: str, new: str) -> None:
        log.append((old, new))
    return _cb


def _recording_tree_refresh(log: list[tuple[Path, Path]]):
    def _cb(old: Path, new: Path) -> None:
        log.append((old, new))
    return _cb


def _always_confirm(*_args) -> bool:
    return True


def _always_cancel(*_args) -> bool:
    return False


def _conflict_overwrite(fname: str) -> tuple[str, bool]:
    return "overwrite", False


def _conflict_skip(fname: str) -> tuple[str, bool]:
    return "skip", False


def _conflict_overwrite_all(fname: str) -> tuple[str, bool]:
    return "overwrite", True


def _conflict_skip_all(fname: str) -> tuple[str, bool]:
    return "skip", True


def _move(
    src: Path,
    dst_parent: Path,
    *,
    confirm_cross_fs=_always_confirm,
    confirm_merge=_always_confirm,
    resolve_conflict=_conflict_skip,
    cache_update=_noop_cache_update,
    tree_refresh=_noop_tree_refresh,
) -> MoveResult:
    """Thin wrapper so tests only specify the callbacks they care about."""
    return move_item(
        src,
        dst_parent,
        confirm_cross_fs=confirm_cross_fs,
        confirm_merge=confirm_merge,
        resolve_conflict=resolve_conflict,
        cache_update=cache_update,
        tree_refresh=tree_refresh,
    )


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------


def test_format_size_bytes() -> None:
    assert format_size(0) == "0.0\u202fB"
    assert format_size(512) == "512.0\u202fB"
    assert format_size(1023) == "1023.0\u202fB"


def test_format_size_kilobytes() -> None:
    assert format_size(1024) == "1.0\u202fKB"
    assert format_size(1536) == "1.5\u202fKB"


def test_format_size_megabytes() -> None:
    assert format_size(1024 ** 2) == "1.0\u202fMB"


def test_format_size_gigabytes() -> None:
    assert format_size(1024 ** 3) == "1.0\u202fGB"


def test_format_size_terabytes() -> None:
    assert format_size(1024 ** 4) == "1.0\u202fTB"


# ---------------------------------------------------------------------------
# compute_total_size
# ---------------------------------------------------------------------------


def test_compute_total_size_single_file(tmp_path: Path) -> None:
    f = tmp_path / "data.bin"
    f.write_bytes(b"x" * 200)
    assert compute_total_size(f) == 200


def test_compute_total_size_directory(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a" * 100)
    (tmp_path / "b.txt").write_bytes(b"b" * 50)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_bytes(b"c" * 25)
    assert compute_total_size(tmp_path) == 175


def test_compute_total_size_empty_directory(tmp_path: Path) -> None:
    assert compute_total_size(tmp_path) == 0


def test_compute_total_size_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"z" * 40)
    link = tmp_path / "link"
    link.symlink_to(target)
    # symlink itself is treated as a single entity; size is the symlink stat size
    result = compute_total_size(link)
    assert result >= 0  # must not raise; exact size is OS-dependent


def test_compute_total_size_nonexistent(tmp_path: Path) -> None:
    assert compute_total_size(tmp_path / "ghost.txt") == 0


# ---------------------------------------------------------------------------
# check_name_collision
# ---------------------------------------------------------------------------


def test_check_name_collision_exists(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hi")
    assert check_name_collision(tmp_path, "file.txt") is True


def test_check_name_collision_missing(tmp_path: Path) -> None:
    assert check_name_collision(tmp_path, "nope.txt") is False


def test_check_name_collision_directory_counts(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    assert check_name_collision(tmp_path, "subdir") is True


# ---------------------------------------------------------------------------
# is_cross_filesystem
# ---------------------------------------------------------------------------


def test_is_cross_filesystem_same_device(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("hi")
    dst = tmp_path / "dst"
    dst.mkdir()
    # Files under the same tmp_path must share a device
    assert is_cross_filesystem(src, dst) is False


def test_is_cross_filesystem_missing_path_returns_false(tmp_path: Path) -> None:
    # os.stat raises OSError → returns False
    assert is_cross_filesystem(tmp_path / "ghost.txt", tmp_path) is False


# ---------------------------------------------------------------------------
# rename_item
# ---------------------------------------------------------------------------


def test_rename_item_success(tmp_path: Path) -> None:
    src = tmp_path / "old.txt"
    src.write_text("content")
    dst = tmp_path / "new.txt"
    result = rename_item(src, dst)
    assert result.success is True
    assert result.error_msg is None
    assert dst.exists()
    assert not src.exists()


def test_rename_item_permission_error(tmp_path: Path) -> None:
    src = tmp_path / "old.txt"
    src.write_text("x")
    with patch("echofinder.services.file_operations.os.rename",
               side_effect=PermissionError):
        result = rename_item(src, tmp_path / "new.txt")
    assert result.success is False
    assert result.error_msg is not None
    assert "Permission denied" in result.error_msg


def test_rename_item_oserror(tmp_path: Path) -> None:
    src = tmp_path / "old.txt"
    src.write_text("x")
    with patch("echofinder.services.file_operations.os.rename",
               side_effect=OSError("disk full")):
        result = rename_item(src, tmp_path / "new.txt")
    assert result.success is False
    assert result.error_msg is not None


# ---------------------------------------------------------------------------
# delete_item
# ---------------------------------------------------------------------------


def test_delete_item_success(tmp_path: Path) -> None:
    target = tmp_path / "todelete.txt"
    target.write_text("bye")
    with patch("send2trash.send2trash"):
        result = delete_item(target)
    assert result.success is True
    assert result.error_msg is None


def test_delete_item_permission_error(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x")
    with patch("send2trash.send2trash", side_effect=PermissionError):
        result = delete_item(target)
    assert result.success is False
    assert "Permission denied" in result.error_msg


def test_delete_item_oserror(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x")
    with patch("send2trash.send2trash", side_effect=OSError):
        result = delete_item(target)
    assert result.success is False
    assert result.error_msg is not None


# ---------------------------------------------------------------------------
# move_item — guards
# ---------------------------------------------------------------------------


def test_move_item_into_itself_fails(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    src.mkdir()
    result = _move(src, src / "sub")
    assert result.success is False
    assert "itself" in result.error_msg


def test_move_item_same_src_and_dst_via_symlink_is_noop(tmp_path: Path) -> None:
    """The same-path noop guard triggers when src and dst resolve identically
    but differ in string representation (e.g. via a symlinked parent directory)."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)
    src = real_dir / "file.txt"
    src.write_text("hi")
    # dst_parent is the symlink; dst resolves to the same inode as src
    result = _move(src, link_dir)
    assert result.success is True
    assert src.exists()


# ---------------------------------------------------------------------------
# move_item — simple file move
# ---------------------------------------------------------------------------


def test_move_item_file_success(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f = src / "file.txt"
    f.write_text("content")
    dst = tmp_path / "dst"
    dst.mkdir()

    cache_log: list = []
    refresh_log: list = []
    result = _move(
        f, dst,
        cache_update=_recording_cache_update(cache_log),
        tree_refresh=_recording_tree_refresh(refresh_log),
    )

    assert result.success is True
    assert (dst / "file.txt").exists()
    assert not f.exists()
    assert cache_log == [(str(f), str(dst / "file.txt"))]
    assert len(refresh_log) == 1


def test_move_item_permission_error_on_rename(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    with patch("echofinder.services.file_operations.os.rename",
               side_effect=PermissionError):
        result = _move(src, dst)
    assert result.success is False
    assert "Permission denied" in result.error_msg


def test_move_item_oserror_on_rename(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    with patch("echofinder.services.file_operations.os.rename",
               side_effect=OSError("disk full")):
        result = _move(src, dst)
    assert result.success is False


# ---------------------------------------------------------------------------
# move_item — file collision
# ---------------------------------------------------------------------------


def test_move_item_collision_skip(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("new")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "file.txt").write_text("existing")

    result = _move(src, dst, resolve_conflict=_conflict_skip)

    assert result.success is False
    assert (dst / "file.txt").read_text() == "existing"


def test_move_item_collision_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("new")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "file.txt").write_text("existing")

    result = _move(src, dst, resolve_conflict=_conflict_overwrite)

    assert result.success is True
    assert (dst / "file.txt").read_text() == "new"


def test_move_item_cross_fs_cancelled(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    with patch("echofinder.services.file_operations.is_cross_filesystem",
               return_value=True):
        result = _move(src, dst, confirm_cross_fs=_always_cancel)
    assert result.success is False
    assert src.exists()


# ---------------------------------------------------------------------------
# move_item — directory merge
# ---------------------------------------------------------------------------


def test_move_item_dir_merge_cancelled(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "folder").mkdir()

    result = _move(src, dst, confirm_merge=_always_cancel)

    assert result.success is False
    assert src.exists()


def test_move_item_dir_merge_success(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")

    dst = tmp_path / "dst"
    dst.mkdir()
    dst_folder = dst / "folder"
    dst_folder.mkdir()
    (dst_folder / "c.txt").write_text("c")

    result = _move(src, dst, confirm_merge=_always_confirm)

    assert result.success is True
    assert result.failures == []
    assert (dst_folder / "a.txt").read_text() == "a"
    assert (dst_folder / "b.txt").read_text() == "b"
    assert (dst_folder / "c.txt").read_text() == "c"
    assert not src.exists()


def test_move_item_dir_merge_conflict_skip(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    src.mkdir()
    (src / "clash.txt").write_text("new")

    dst = tmp_path / "dst"
    dst.mkdir()
    dst_folder = dst / "folder"
    dst_folder.mkdir()
    (dst_folder / "clash.txt").write_text("original")

    result = _move(
        src, dst,
        confirm_merge=_always_confirm,
        resolve_conflict=_conflict_skip,
    )

    assert result.success is True
    assert (dst_folder / "clash.txt").read_text() == "original"


def test_move_item_dir_merge_conflict_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    src.mkdir()
    (src / "clash.txt").write_text("new")

    dst = tmp_path / "dst"
    dst.mkdir()
    dst_folder = dst / "folder"
    dst_folder.mkdir()
    (dst_folder / "clash.txt").write_text("original")

    result = _move(
        src, dst,
        confirm_merge=_always_confirm,
        resolve_conflict=_conflict_overwrite,
    )

    assert result.success is True
    assert (dst_folder / "clash.txt").read_text() == "new"


def test_move_item_dir_merge_apply_to_all_skip(tmp_path: Path) -> None:
    """'apply to all: skip' means the callback is only invoked once."""
    src = tmp_path / "folder"
    src.mkdir()
    (src / "a.txt").write_text("new-a")
    (src / "b.txt").write_text("new-b")

    dst = tmp_path / "dst"
    dst.mkdir()
    dst_folder = dst / "folder"
    dst_folder.mkdir()
    (dst_folder / "a.txt").write_text("orig-a")
    (dst_folder / "b.txt").write_text("orig-b")

    call_count = 0

    def _skip_all(fname: str) -> tuple[str, bool]:
        nonlocal call_count
        call_count += 1
        return "skip", True

    _move(src, dst, confirm_merge=_always_confirm, resolve_conflict=_skip_all)

    assert call_count == 1
    assert (dst_folder / "a.txt").read_text() == "orig-a"
    assert (dst_folder / "b.txt").read_text() == "orig-b"


def test_move_item_dir_merge_apply_to_all_overwrite(tmp_path: Path) -> None:
    """'apply to all: overwrite' replaces every conflicting file without prompting again."""
    src = tmp_path / "folder"
    src.mkdir()
    (src / "a.txt").write_text("new-a")
    (src / "b.txt").write_text("new-b")

    dst = tmp_path / "dst"
    dst.mkdir()
    dst_folder = dst / "folder"
    dst_folder.mkdir()
    (dst_folder / "a.txt").write_text("orig-a")
    (dst_folder / "b.txt").write_text("orig-b")

    call_count = 0

    def _overwrite_all(fname: str) -> tuple[str, bool]:
        nonlocal call_count
        call_count += 1
        return "overwrite", True

    _move(src, dst, confirm_merge=_always_confirm, resolve_conflict=_overwrite_all)

    assert call_count == 1
    assert (dst_folder / "a.txt").read_text() == "new-a"
    assert (dst_folder / "b.txt").read_text() == "new-b"


def test_move_item_dir_merge_cache_updated_for_each_file(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    src.mkdir()
    (src / "x.txt").write_text("x")
    (src / "y.txt").write_text("y")

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "folder").mkdir()

    cache_log: list = []
    _move(
        src, dst,
        confirm_merge=_always_confirm,
        cache_update=_recording_cache_update(cache_log),
    )

    moved_names = {Path(new).name for _, new in cache_log}
    assert moved_names == {"x.txt", "y.txt"}


def test_move_item_dir_merge_nested_subdirectory(tmp_path: Path) -> None:
    src = tmp_path / "folder"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "deep.txt").write_text("deep")

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "folder").mkdir()

    result = _move(src, dst, confirm_merge=_always_confirm)

    assert result.success is True
    assert (dst / "folder" / "sub" / "deep.txt").read_text() == "deep"
