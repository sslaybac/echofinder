"""Tests for _compute_hash — no Qt, no UI.

The hashing_engine module imports PyQt6 at the top level, so Qt is stubbed in
sys.modules before the module is imported. Only the pure helper function
_compute_hash is exercised here; the Qt-dependent class code is never reached.

Run with:  uv run pytest tests/test_compute_hash.py -v
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub Qt before importing the module under test
# ---------------------------------------------------------------------------
for _mod in ("PyQt6", "PyQt6.QtCore"):
    sys.modules.setdefault(_mod, MagicMock())

from echofinder.services.hashing_engine import _compute_hash  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_known_content(tmp_path: Path) -> None:
    data = b"hello, echofinder"
    f = tmp_path / "file.txt"
    f.write_bytes(data)
    assert _compute_hash(str(f)) == _sha256_of(data)


def test_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    assert _compute_hash(str(f)) == _sha256_of(b"")


def test_multi_chunk_file(tmp_path: Path) -> None:
    """A file larger than the 64 KB read chunk must hash identically to hashlib."""
    data = b"x" * (65536 * 3 + 1)  # spans 4 read() calls
    f = tmp_path / "large.bin"
    f.write_bytes(data)
    assert _compute_hash(str(f)) == _sha256_of(data)


def test_binary_content(tmp_path: Path) -> None:
    data = bytes(range(256)) * 16
    f = tmp_path / "binary.bin"
    f.write_bytes(data)
    assert _compute_hash(str(f)) == _sha256_of(data)


def test_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _compute_hash(str(tmp_path / "ghost.bin"))


def test_returns_lowercase_hex(tmp_path: Path) -> None:
    f = tmp_path / "file.bin"
    f.write_bytes(b"\xff\xfe\xfd")
    result = _compute_hash(str(f))
    assert result == result.lower()
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
