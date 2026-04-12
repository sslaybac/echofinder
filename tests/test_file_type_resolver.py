"""Standalone tests for FileTypeResolver — no Qt, no UI.

Run with:  uv run pytest tests/test_file_type_resolver.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from echofinder.models.file_node import FileType
from echofinder.services.file_type import FileTypeResolver


@pytest.fixture
def resolver() -> FileTypeResolver:
    return FileTypeResolver()


# ---------------------------------------------------------------------------
# Structural types
# ---------------------------------------------------------------------------


def test_resolve_directory(tmp_path: Path, resolver: FileTypeResolver) -> None:
    d = tmp_path / "mydir"
    d.mkdir()
    assert resolver.resolve(d) == FileType.FOLDER


def test_resolve_symlink_internal(tmp_path: Path, resolver: FileTypeResolver) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert resolver.resolve(link, root=tmp_path) == FileType.SYMLINK_INTERNAL


def test_resolve_symlink_external(tmp_path: Path, resolver: FileTypeResolver) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "target.txt"
    target.write_text("hello")
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    link = root_dir / "link"
    link.symlink_to(target)
    assert resolver.resolve(link, root=root_dir) == FileType.SYMLINK_EXTERNAL


def test_resolve_symlink_no_root_is_external(tmp_path: Path, resolver: FileTypeResolver) -> None:
    """When root is None, all symlinks are treated as external."""
    target = tmp_path / "target.txt"
    target.write_text("hello")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert resolver.resolve(link, root=None) == FileType.SYMLINK_EXTERNAL


def test_resolve_symlink_to_directory_is_symlink_not_folder(
    tmp_path: Path, resolver: FileTypeResolver
) -> None:
    """Symlinks are resolved before the directory check — returns SYMLINK_INTERNAL."""
    d = tmp_path / "subdir"
    d.mkdir()
    link = tmp_path / "linktodir"
    link.symlink_to(d)
    assert resolver.resolve(link, root=tmp_path) == FileType.SYMLINK_INTERNAL


def test_resolve_symlink_even_if_looks_like_image(
    tmp_path: Path, resolver: FileTypeResolver
) -> None:
    """A symlink to a JPEG must resolve as SYMLINK, not IMAGE (precedence check)."""
    target = tmp_path / "photo.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 100)
    link = tmp_path / "link.jpg"
    link.symlink_to(target)
    assert resolver.resolve(link, root=tmp_path) == FileType.SYMLINK_INTERNAL


# ---------------------------------------------------------------------------
# Image types
# ---------------------------------------------------------------------------


def test_resolve_jpeg(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00" + b"\x00" * 100)
    assert resolver.resolve(f) == FileType.IMAGE


def test_resolve_png(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 100)
    assert resolver.resolve(f) == FileType.IMAGE


def test_resolve_gif(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "anim.gif"
    f.write_bytes(b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 20)
    assert resolver.resolve(f) == FileType.IMAGE


# ---------------------------------------------------------------------------
# Text / code types
# ---------------------------------------------------------------------------


def test_resolve_python_code(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "script.py"
    f.write_text("def main(): pass\n")
    assert resolver.resolve(f) == FileType.CODE


def test_resolve_javascript_code(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "app.js"
    f.write_text("console.log('hi');")
    assert resolver.resolve(f) == FileType.CODE


def test_resolve_plain_text(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("some text content")
    assert resolver.resolve(f) == FileType.TEXT


def test_resolve_markdown(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "readme.md"
    f.write_text("# Title\n\nSome content.")
    assert resolver.resolve(f) == FileType.TEXT


# ---------------------------------------------------------------------------
# Deferred types (routed to unsupported widget in Stage 4)
# ---------------------------------------------------------------------------


def test_resolve_pdf_by_extension(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n%comment\n")
    assert resolver.resolve(f) == FileType.PDF


def test_resolve_audio_mp3_by_extension(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "song.mp3"
    f.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert resolver.resolve(f) == FileType.AUDIO


def test_resolve_video_mp4_by_extension(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "video.mp4"
    # Valid ftyp box header so magic identifies it as video/mp4
    f.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomavc1" + b"\x00" * 100)
    assert resolver.resolve(f) == FileType.VIDEO


# ---------------------------------------------------------------------------
# Unknown / unrecognised
# ---------------------------------------------------------------------------


def test_resolve_unknown_extension(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "data.xyz999"
    f.write_bytes(b"\x00\x01\x02\x03\x04")
    assert resolver.resolve(f) == FileType.UNKNOWN


def test_resolve_nonexistent_is_unknown(tmp_path: Path, resolver: FileTypeResolver) -> None:
    f = tmp_path / "does_not_exist.txt"
    # path.is_file() returns False → falls through to UNKNOWN
    assert resolver.resolve(f) == FileType.UNKNOWN
