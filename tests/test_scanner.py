"""Tests for echofinder.models.scanner — no Qt, no UI.

Run with:  uv run pytest tests/test_scanner.py -v
"""
from __future__ import annotations

import os as _os
from pathlib import Path

import pytest

import echofinder.models.scanner as _scanner
from echofinder.models.file_node import FileNode, FileType, HashState, OwnershipState, PermissionState
from echofinder.models.scanner import (
    _evaluate_ownership,
    _evaluate_permission,
    _initial_hash_state,
    scan_directory,
    walk_files,
)
from echofinder.services.file_type import FileTypeResolver


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver() -> FileTypeResolver:
    return FileTypeResolver()


def _node(path: Path) -> FileNode:
    return FileNode(path=path, file_type=FileType.FOLDER)


# ---------------------------------------------------------------------------
# walk_files
# ---------------------------------------------------------------------------


def test_walk_files_yields_files_recursively(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("b")
    assert set(walk_files(tmp_path)) == {tmp_path / "a.txt", sub / "b.txt"}


def test_walk_files_excludes_directories(tmp_path: Path) -> None:
    (tmp_path / "mydir").mkdir()
    (tmp_path / "file.txt").write_text("x")
    assert all(not p.is_dir() for p in walk_files(tmp_path))


def test_walk_files_empty_dir_yields_nothing(tmp_path: Path) -> None:
    assert list(walk_files(tmp_path)) == []


def test_walk_files_includes_file_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hi")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    assert link in set(walk_files(tmp_path))


def test_walk_files_does_not_recurse_into_symlink_dirs(tmp_path: Path) -> None:
    """Contents of a symlinked directory must not appear via the symlink path."""
    subdir = tmp_path / "real"
    subdir.mkdir()
    (subdir / "deep.txt").write_text("x")
    link = tmp_path / "link_to_real"
    link.symlink_to(subdir)
    results = set(walk_files(tmp_path))
    assert subdir / "deep.txt" in results
    assert link / "deep.txt" not in results


# ---------------------------------------------------------------------------
# _evaluate_ownership
# ---------------------------------------------------------------------------


def test_evaluate_ownership_individual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    st = f.stat()
    monkeypatch.setattr(_scanner, "_CURRENT_UID", st.st_uid)
    monkeypatch.setattr(_scanner, "_CURRENT_GIDS", frozenset())
    assert _evaluate_ownership(st) == OwnershipState.INDIVIDUAL


def test_evaluate_ownership_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    st = f.stat()
    monkeypatch.setattr(_scanner, "_CURRENT_UID", st.st_uid + 9999)
    monkeypatch.setattr(_scanner, "_CURRENT_GIDS", frozenset({st.st_gid}))
    assert _evaluate_ownership(st) == OwnershipState.GROUP


def test_evaluate_ownership_neither(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    st = f.stat()
    monkeypatch.setattr(_scanner, "_CURRENT_UID", st.st_uid + 9999)
    monkeypatch.setattr(_scanner, "_CURRENT_GIDS", frozenset({st.st_gid + 9999}))
    assert _evaluate_ownership(st) == OwnershipState.NEITHER


def test_evaluate_ownership_individual_beats_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UID match takes priority even when GID also matches."""
    f = tmp_path / "f.txt"
    f.write_text("x")
    st = f.stat()
    monkeypatch.setattr(_scanner, "_CURRENT_UID", st.st_uid)
    monkeypatch.setattr(_scanner, "_CURRENT_GIDS", frozenset({st.st_gid}))
    assert _evaluate_ownership(st) == OwnershipState.INDIVIDUAL


# ---------------------------------------------------------------------------
# _evaluate_permission
# ---------------------------------------------------------------------------


def test_evaluate_permission_read_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    monkeypatch.setattr(_scanner.os, "access", lambda _p, _m: True)
    assert _evaluate_permission(f) == PermissionState.READ_WRITE


def test_evaluate_permission_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    monkeypatch.setattr(_scanner.os, "access", lambda _p, mode: mode == _os.R_OK)
    assert _evaluate_permission(f) == PermissionState.READ_ONLY


def test_evaluate_permission_not_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    monkeypatch.setattr(_scanner.os, "access", lambda _p, _m: False)
    assert _evaluate_permission(f) == PermissionState.NOT_READABLE


def test_evaluate_permission_write_only_is_not_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write-only files collapse to NOT_READABLE — there is no separate write-only state."""
    f = tmp_path / "f.txt"
    f.write_text("x")
    monkeypatch.setattr(_scanner.os, "access", lambda _p, mode: mode == _os.W_OK)
    assert _evaluate_permission(f) == PermissionState.NOT_READABLE


# ---------------------------------------------------------------------------
# _initial_hash_state
# ---------------------------------------------------------------------------


def test_initial_hash_state_folder_yields_unique() -> None:
    assert _initial_hash_state(FileType.FOLDER, PermissionState.READ_WRITE) == HashState.UNIQUE


def test_initial_hash_state_symlink_internal_yields_unique() -> None:
    assert _initial_hash_state(FileType.SYMLINK_INTERNAL, PermissionState.READ_WRITE) == HashState.UNIQUE


def test_initial_hash_state_symlink_external_yields_unique() -> None:
    assert _initial_hash_state(FileType.SYMLINK_EXTERNAL, PermissionState.READ_WRITE) == HashState.UNIQUE


def test_initial_hash_state_unreadable_yields_unique() -> None:
    """Unreadable files must start with UNIQUE — no permanent hourglass (mutual-exclusivity rule)."""
    for ft in (FileType.TEXT, FileType.CODE, FileType.IMAGE, FileType.PDF, FileType.EPUB, FileType.UNKNOWN):
        assert _initial_hash_state(ft, PermissionState.NOT_READABLE) == HashState.UNIQUE, ft


def test_initial_hash_state_readable_file_yields_not_hashed() -> None:
    for ft in (FileType.TEXT, FileType.CODE, FileType.IMAGE, FileType.PDF, FileType.EPUB):
        for perm in (PermissionState.READ_WRITE, PermissionState.READ_ONLY):
            assert _initial_hash_state(ft, perm) == HashState.NOT_HASHED, (ft, perm)


# ---------------------------------------------------------------------------
# scan_directory
# ---------------------------------------------------------------------------


def test_scan_directory_empty_dir(tmp_path: Path, resolver: FileTypeResolver) -> None:
    assert scan_directory(_node(tmp_path), tmp_path, resolver) == []


def test_scan_directory_oserror_returns_empty(tmp_path: Path, resolver: FileTypeResolver) -> None:
    missing = tmp_path / "does_not_exist"
    assert scan_directory(FileNode(path=missing, file_type=FileType.FOLDER), tmp_path, resolver) == []


def test_scan_directory_dirs_sorted_before_files(tmp_path: Path, resolver: FileTypeResolver) -> None:
    (tmp_path / "a_file.txt").write_text("x")
    (tmp_path / "z_dir").mkdir()
    children = scan_directory(_node(tmp_path), tmp_path, resolver)
    assert children[0].file_type == FileType.FOLDER


def test_scan_directory_dirs_sorted_case_insensitively(
    tmp_path: Path, resolver: FileTypeResolver
) -> None:
    for name in ("Zoo", "apple", "Mango"):
        (tmp_path / name).mkdir()
    children = scan_directory(_node(tmp_path), tmp_path, resolver)
    names = [c.name for c in children]
    assert names == sorted(names, key=str.lower)


def test_scan_directory_files_sorted_case_insensitively(
    tmp_path: Path, resolver: FileTypeResolver
) -> None:
    for name in ("Beta.txt", "alpha.txt", "GAMMA.txt"):
        (tmp_path / name).write_text(name)
    children = scan_directory(_node(tmp_path), tmp_path, resolver)
    names = [c.name for c in children]
    assert names == sorted(names, key=str.lower)


def test_scan_directory_sets_parent_and_row(tmp_path: Path, resolver: FileTypeResolver) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name)
    node = _node(tmp_path)
    children = scan_directory(node, tmp_path, resolver)
    for i, child in enumerate(children):
        assert child.parent is node
        assert child.row == i


def test_scan_directory_readable_file_starts_not_hashed(
    tmp_path: Path, resolver: FileTypeResolver
) -> None:
    (tmp_path / "readable.txt").write_text("hello")
    children = scan_directory(_node(tmp_path), tmp_path, resolver)
    assert len(children) == 1
    assert children[0].hash_state == HashState.NOT_HASHED


def test_scan_directory_unreadable_file_starts_unique(
    tmp_path: Path, resolver: FileTypeResolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreadable files must not show the hourglass; hash_state starts as UNIQUE."""
    (tmp_path / "locked.txt").write_text("x")
    monkeypatch.setattr(_scanner.os, "access", lambda _p, _m: False)
    children = scan_directory(_node(tmp_path), tmp_path, resolver)
    assert len(children) == 1
    assert children[0].permission == PermissionState.NOT_READABLE
    assert children[0].hash_state == HashState.UNIQUE


def test_scan_directory_not_recursive(tmp_path: Path, resolver: FileTypeResolver) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("x")
    children = scan_directory(_node(tmp_path), tmp_path, resolver)
    assert len(children) == 1
    assert children[0].file_type == FileType.FOLDER
    assert children[0].children is None
