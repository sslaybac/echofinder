"""Tests for echofinder.models.session.SessionState — no Qt, no UI.

Run with:  uv run pytest tests/test_session.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from echofinder.models.session import SessionState


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def make_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[], SessionState]:
    """Return a factory that creates SessionState instances backed by tmp_path."""
    monkeypatch.setattr("platformdirs.user_config_dir", lambda _: str(tmp_path))
    return SessionState


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def test_init_creates_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "new_dir"
    monkeypatch.setattr("platformdirs.user_config_dir", lambda _: str(config_dir))
    SessionState()
    assert config_dir.exists()


def test_init_fresh_session_has_no_root(make_session: Callable[[], SessionState]) -> None:
    assert make_session().get_root() is None


def test_init_fresh_session_has_empty_expansion_state(make_session: Callable[[], SessionState]) -> None:
    assert make_session().get_expansion_state() == []


# ---------------------------------------------------------------------------
# _load — error handling
# ---------------------------------------------------------------------------


def test_load_returns_empty_on_missing_file(make_session: Callable[[], SessionState]) -> None:
    assert make_session().get_root() is None


def test_load_returns_empty_on_corrupt_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platformdirs.user_config_dir", lambda _: str(tmp_path))
    (tmp_path / "session.json").write_text("this is not json {{{")
    s = SessionState()
    assert s.get_root() is None
    assert s.get_expansion_state() == []


def test_load_returns_empty_when_json_is_not_a_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platformdirs.user_config_dir", lambda _: str(tmp_path))
    (tmp_path / "session.json").write_text(json.dumps(["/a", "/b"]))
    s = SessionState()
    assert s.get_root() is None


# ---------------------------------------------------------------------------
# get_root / set_root
# ---------------------------------------------------------------------------


def test_get_root_returns_none_when_unset(make_session: Callable[[], SessionState]) -> None:
    assert make_session().get_root() is None


def test_set_root_updates_in_memory(make_session: Callable[[], SessionState]) -> None:
    s = make_session()
    s.set_root("/home/user/docs")
    assert s.get_root() == "/home/user/docs"


def test_set_root_persists_to_disk(make_session: Callable[[], SessionState]) -> None:
    s1 = make_session()
    s1.set_root("/data/files")
    s2 = make_session()
    assert s2.get_root() == "/data/files"


def test_set_root_overwrites_previous(make_session: Callable[[], SessionState]) -> None:
    s = make_session()
    s.set_root("/first")
    s.set_root("/second")
    assert s.get_root() == "/second"


def test_set_root_overwrite_persists_to_disk(make_session: Callable[[], SessionState]) -> None:
    s1 = make_session()
    s1.set_root("/first")
    s1.set_root("/second")
    s2 = make_session()
    assert s2.get_root() == "/second"


# ---------------------------------------------------------------------------
# get_expansion_state / set_expansion_state
# ---------------------------------------------------------------------------


def test_get_expansion_state_returns_empty_when_unset(make_session: Callable[[], SessionState]) -> None:
    assert make_session().get_expansion_state() == []


def test_set_expansion_state_updates_in_memory(make_session: Callable[[], SessionState]) -> None:
    s = make_session()
    paths = ["/a", "/b", "/c"]
    s.set_expansion_state(paths)
    assert s.get_expansion_state() == paths


def test_set_expansion_state_persists_to_disk(make_session: Callable[[], SessionState]) -> None:
    paths = ["/x/y", "/x/z"]
    s1 = make_session()
    s1.set_expansion_state(paths)
    s2 = make_session()
    assert s2.get_expansion_state() == paths


def test_get_expansion_state_returns_empty_for_non_list_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt stored value (not a list) must degrade gracefully to []."""
    monkeypatch.setattr("platformdirs.user_config_dir", lambda _: str(tmp_path))
    (tmp_path / "session.json").write_text(json.dumps({"expanded_paths": "not a list"}))
    assert SessionState().get_expansion_state() == []


# ---------------------------------------------------------------------------
# clear_expansion_state
# ---------------------------------------------------------------------------


def test_clear_expansion_state_removes_expanded_paths(make_session: Callable[[], SessionState]) -> None:
    s = make_session()
    s.set_expansion_state(["/a", "/b"])
    s.clear_expansion_state()
    assert s.get_expansion_state() == []


def test_clear_expansion_state_persists_to_disk(make_session: Callable[[], SessionState]) -> None:
    s1 = make_session()
    s1.set_expansion_state(["/a"])
    s1.clear_expansion_state()
    s2 = make_session()
    assert s2.get_expansion_state() == []


def test_clear_expansion_state_preserves_root(make_session: Callable[[], SessionState]) -> None:
    s = make_session()
    s.set_root("/my/root")
    s.set_expansion_state(["/a"])
    s.clear_expansion_state()
    assert s.get_root() == "/my/root"


def test_clear_expansion_state_is_idempotent(make_session: Callable[[], SessionState]) -> None:
    s = make_session()
    s.clear_expansion_state()  # called with no prior set — must not raise
    assert s.get_expansion_state() == []
