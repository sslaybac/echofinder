"""Tests for preview.read_text_for_preview — no Qt, no UI.

Run with:  uv run pytest tests/test_preview.py -v
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from echofinder.services.preview import read_text_for_preview


# ---------------------------------------------------------------------------
# UTF-8 path (step 1)
# ---------------------------------------------------------------------------


def test_ascii_returns_utf8(tmp_path: Path) -> None:
    f = tmp_path / "ascii.txt"
    f.write_bytes(b"hello world")
    text, enc = read_text_for_preview(f)
    assert text == "hello world"
    assert enc == "UTF-8"


def test_utf8_multibyte_returns_utf8(tmp_path: Path) -> None:
    content = "café résumé naïve"
    f = tmp_path / "utf8.txt"
    f.write_bytes(content.encode("utf-8"))
    text, enc = read_text_for_preview(f)
    assert text == content
    assert enc == "UTF-8"


def test_empty_file_returns_utf8(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_bytes(b"")
    text, enc = read_text_for_preview(f)
    assert text == ""
    assert enc == "UTF-8"


# ---------------------------------------------------------------------------
# charset-normalizer path (step 2)
# ---------------------------------------------------------------------------


def _make_charset_result(encoding: str, decoded: str):
    """Build a minimal charset_normalizer result mock."""
    best = MagicMock()
    best.encoding = encoding
    results = MagicMock()
    results.best.return_value = best
    return results, decoded


def test_charset_normalizer_detection(tmp_path: Path) -> None:
    """When UTF-8 fails, charset-normalizer's suggestion is used."""
    raw = "naïve".encode("latin-1")  # fails UTF-8
    f = tmp_path / "latin.txt"
    f.write_bytes(raw)

    results_mock, _ = _make_charset_result("ISO-8859-1", "naïve")
    with patch("charset_normalizer.from_bytes", return_value=results_mock):
        text, enc = read_text_for_preview(f)

    assert enc == "ISO-8859-1"
    assert text == raw.decode("ISO-8859-1")


def test_charset_normalizer_none_result_falls_to_latin1(tmp_path: Path) -> None:
    """When charset-normalizer returns no best match, Latin-1 is used."""
    raw = b"\x80\x99\xa3"  # non-UTF-8 bytes
    f = tmp_path / "unknown.bin"
    f.write_bytes(raw)

    results_mock = MagicMock()
    results_mock.best.return_value = None
    with patch("charset_normalizer.from_bytes", return_value=results_mock):
        text, enc = read_text_for_preview(f)

    assert enc == "Latin-1"
    assert text == raw.decode("latin-1")


def test_charset_normalizer_utf16_suggestion_skipped(tmp_path: Path) -> None:
    """UTF-16/UTF-32 suggestions from charset-normalizer are ignored to avoid
    false positives on short content; the function falls through to Latin-1."""
    raw = b"\xff\xfe\x41\x00"  # UTF-16 LE BOM + 'A' — fails UTF-8
    f = tmp_path / "utf16.bin"
    f.write_bytes(raw)

    results_mock, _ = _make_charset_result("utf_16", "A")
    with patch("charset_normalizer.from_bytes", return_value=results_mock):
        text, enc = read_text_for_preview(f)

    assert enc == "Latin-1"
    assert text == raw.decode("latin-1")


def test_charset_normalizer_utf32_suggestion_skipped(tmp_path: Path) -> None:
    raw = b"\x00\x00\xfe\xff\x00\x00\x00\x41"  # UTF-32 BE BOM
    f = tmp_path / "utf32.bin"
    f.write_bytes(raw)

    results_mock, _ = _make_charset_result("utf_32", "A")
    with patch("charset_normalizer.from_bytes", return_value=results_mock):
        text, enc = read_text_for_preview(f)

    assert enc == "Latin-1"


# ---------------------------------------------------------------------------
# Latin-1 fallback (step 3)
# ---------------------------------------------------------------------------


def test_latin1_fallback_when_charset_normalizer_unavailable(tmp_path: Path) -> None:
    """ImportError from charset-normalizer falls through to Latin-1."""
    raw = b"\x80\x99\xa3"
    f = tmp_path / "file.bin"
    f.write_bytes(raw)

    with patch.dict("sys.modules", {"charset_normalizer": None}):
        text, enc = read_text_for_preview(f)

    assert enc == "Latin-1"
    assert text == raw.decode("latin-1")


def test_latin1_covers_all_byte_values(tmp_path: Path) -> None:
    """Latin-1 must never raise — all 256 byte values are valid."""
    raw = bytes(range(256))
    f = tmp_path / "allbytes.bin"
    f.write_bytes(raw)

    results_mock = MagicMock()
    results_mock.best.return_value = None
    with patch("charset_normalizer.from_bytes", return_value=results_mock):
        text, enc = read_text_for_preview(f)

    assert enc == "Latin-1"
    assert len(text) == 256


# ---------------------------------------------------------------------------
# OSError
# ---------------------------------------------------------------------------


def test_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_text_for_preview(tmp_path / "ghost.txt")
