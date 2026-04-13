"""Tests for _fmt_ms — no Qt, no VLC.

audio_widget imports PyQt6 at the top level, so Qt is stubbed in sys.modules
before the module is imported. Only the pure helper function _fmt_ms is
exercised here; the Qt-dependent class code is never reached.

Run with:  uv run pytest tests/test_fmt_ms.py -v
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub Qt and VLC before importing the module under test
# ---------------------------------------------------------------------------

for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "vlc"):
    sys.modules.setdefault(_mod, MagicMock())

from echofinder.ui.preview.audio_widget import _fmt_ms  # noqa: E402


# ---------------------------------------------------------------------------
# Zero and sub-second values
# ---------------------------------------------------------------------------


def test_zero() -> None:
    assert _fmt_ms(0) == "0:00"


def test_less_than_one_second() -> None:
    assert _fmt_ms(999) == "0:00"


# ---------------------------------------------------------------------------
# Sub-minute values
# ---------------------------------------------------------------------------


def test_one_second() -> None:
    assert _fmt_ms(1000) == "0:01"


def test_nine_seconds() -> None:
    assert _fmt_ms(9000) == "0:09"


def test_seconds_zero_padded() -> None:
    # Single-digit seconds must be zero-padded to two digits.
    assert _fmt_ms(5000) == "0:05"


def test_59_seconds() -> None:
    assert _fmt_ms(59000) == "0:59"


# ---------------------------------------------------------------------------
# Exact minute boundaries
# ---------------------------------------------------------------------------


def test_one_minute_exact() -> None:
    assert _fmt_ms(60_000) == "1:00"


def test_two_minutes_exact() -> None:
    assert _fmt_ms(120_000) == "2:00"


# ---------------------------------------------------------------------------
# Mixed minutes and seconds
# ---------------------------------------------------------------------------


def test_one_minute_one_second() -> None:
    assert _fmt_ms(61_000) == "1:01"


def test_three_minutes_seven_seconds() -> None:
    assert _fmt_ms(187_000) == "3:07"


def test_seconds_still_zero_padded_past_one_minute() -> None:
    assert _fmt_ms(65_000) == "1:05"


# ---------------------------------------------------------------------------
# Multi-digit minutes
# ---------------------------------------------------------------------------


def test_ten_minutes() -> None:
    assert _fmt_ms(600_000) == "10:00"


def test_large_value() -> None:
    # 1 hour, 23 minutes, 45 seconds
    assert _fmt_ms(5_025_000) == "83:45"
