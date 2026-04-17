"""Tests for HashCache — no Qt, no UI.

Run with:  uv run pytest tests/test_hash_cache.py -v
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from echofinder.models.hash_cache import HashCache


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HashCache:
    """Return a HashCache backed by a temporary directory."""
    monkeypatch.setattr(
        "echofinder.models.hash_cache.user_config_dir",
        lambda _app_name: str(tmp_path),
    )
    return HashCache()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def test_init_creates_db_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "echofinder.models.hash_cache.user_config_dir",
        lambda _: str(tmp_path),
    )
    HashCache()
    assert (tmp_path / "cache.db").exists()


def test_was_reset_false_on_clean_open(cache: HashCache) -> None:
    assert cache.was_reset is False


def test_was_reset_true_after_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "echofinder.models.hash_cache.user_config_dir",
        lambda _: str(tmp_path),
    )
    # Write garbage so SQLite rejects the file
    (tmp_path / "cache.db").write_bytes(b"this is not a sqlite database")
    corrupt_cache = HashCache()
    assert corrupt_cache.was_reset is True


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def test_lookup_miss_on_empty_cache(cache: HashCache) -> None:
    assert cache.lookup("/some/file.txt", 100, 1234567890.0) is None


def test_lookup_hit_after_store(cache: HashCache) -> None:
    cache.store("/a/b.txt", 42, 1.0, "abc123", "text/plain", None)
    result = cache.lookup("/a/b.txt", 42, 1.0)
    assert result is not None and result[0] == "abc123"


def test_lookup_miss_wrong_size(cache: HashCache) -> None:
    cache.store("/a/b.txt", 42, 1.0, "abc123", None, None)
    assert cache.lookup("/a/b.txt", 99, 1.0) is None


def test_lookup_miss_wrong_mtime(cache: HashCache) -> None:
    cache.store("/a/b.txt", 42, 1.0, "abc123", None, None)
    assert cache.lookup("/a/b.txt", 42, 2.0) is None


def test_lookup_miss_wrong_path(cache: HashCache) -> None:
    cache.store("/a/b.txt", 42, 1.0, "abc123", None, None)
    assert cache.lookup("/a/c.txt", 42, 1.0) is None


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_replaces_existing_entry(cache: HashCache) -> None:
    cache.store("/f.txt", 10, 1.0, "oldhash", "text/plain", None)
    cache.store("/f.txt", 10, 1.0, "newhash", "text/plain", None)
    result = cache.lookup("/f.txt", 10, 1.0)
    assert result is not None and result[0] == "newhash"


def test_store_preserves_filetype_and_language(cache: HashCache) -> None:
    cache.store("/script.py", 50, 2.0, "deadbeef", "text/x-python", "Python")
    meta = cache.get_file_metadata("/script.py")
    assert meta is not None
    assert meta["filetype"] == "text/x-python"
    assert meta["language"] == "Python"


def test_store_accepts_none_filetype_and_language(cache: HashCache) -> None:
    cache.store("/unknown.bin", 8, 3.0, "cafebabe", None, None)
    meta = cache.get_file_metadata("/unknown.bin")
    assert meta is not None
    assert meta["filetype"] is None
    assert meta["language"] is None


# ---------------------------------------------------------------------------
# get_file_metadata
# ---------------------------------------------------------------------------


def test_get_file_metadata_returns_none_for_unknown(cache: HashCache) -> None:
    assert cache.get_file_metadata("/no/such/file.txt") is None


def test_get_file_metadata_returns_dict(cache: HashCache) -> None:
    cache.store("/doc.pdf", 1024, 9.0, "hashval", "application/pdf", None)
    result = cache.get_file_metadata("/doc.pdf")
    assert result == {"hash": "hashval", "filetype": "application/pdf", "language": None}


# ---------------------------------------------------------------------------
# get_duplicate_paths
# ---------------------------------------------------------------------------


def test_get_duplicate_paths_empty_hash_returns_empty(cache: HashCache) -> None:
    assert cache.get_duplicate_paths("", "/any/path") == []


def test_get_duplicate_paths_no_duplicates(cache: HashCache) -> None:
    cache.store("/a.txt", 1, 1.0, "unique", None, None)
    assert cache.get_duplicate_paths("unique", "/a.txt") == []


def test_get_duplicate_paths_finds_matches(cache: HashCache) -> None:
    cache.store("/a.txt", 1, 1.0, "shared", None, None)
    cache.store("/b.txt", 1, 2.0, "shared", None, None)
    cache.store("/c.txt", 1, 3.0, "shared", None, None)
    dupes = cache.get_duplicate_paths("shared", "/a.txt")
    assert sorted(dupes) == ["/b.txt", "/c.txt"]


def test_get_duplicate_paths_excludes_self(cache: HashCache) -> None:
    cache.store("/a.txt", 1, 1.0, "shared", None, None)
    cache.store("/b.txt", 1, 2.0, "shared", None, None)
    dupes = cache.get_duplicate_paths("shared", "/a.txt")
    assert "/a.txt" not in dupes


def test_get_duplicate_paths_ignores_different_hash(cache: HashCache) -> None:
    cache.store("/a.txt", 1, 1.0, "hash1", None, None)
    cache.store("/b.txt", 1, 2.0, "hash2", None, None)
    assert cache.get_duplicate_paths("hash1", "/a.txt") == []


# ---------------------------------------------------------------------------
# get_cached_stat
# ---------------------------------------------------------------------------


def test_get_cached_stat_returns_none_for_unknown(cache: HashCache) -> None:
    assert cache.get_cached_stat("/no/such/file") is None


def test_get_cached_stat_returns_size_and_mtime(cache: HashCache) -> None:
    cache.store("/data.bin", 512, 99.5, "h", None, None)
    result = cache.get_cached_stat("/data.bin")
    assert result == (512, 99.5)


# ---------------------------------------------------------------------------
# update_path
# ---------------------------------------------------------------------------


def test_update_path_moves_entry(cache: HashCache) -> None:
    cache.store("/old.txt", 10, 1.0, "myhash", None, None)
    cache.update_path("/old.txt", "/new.txt")
    assert cache.lookup("/old.txt", 10, 1.0) is None
    result = cache.lookup("/new.txt", 10, 1.0)
    assert result is not None and result[0] == "myhash"


def test_update_path_preserves_metadata(cache: HashCache) -> None:
    cache.store("/old.py", 20, 2.0, "h2", "text/x-python", "Python")
    cache.update_path("/old.py", "/new.py")
    meta = cache.get_file_metadata("/new.py")
    assert meta == {"hash": "h2", "filetype": "text/x-python", "language": "Python"}


def test_update_path_nonexistent_is_noop(cache: HashCache) -> None:
    cache.update_path("/does/not/exist.txt", "/also/not/there.txt")
    assert cache.get_file_metadata("/also/not/there.txt") is None


# ---------------------------------------------------------------------------
# remove_path
# ---------------------------------------------------------------------------


def test_remove_path_deletes_entry(cache: HashCache) -> None:
    cache.store("/todelete.txt", 5, 1.0, "gone", None, None)
    cache.remove_path("/todelete.txt")
    assert cache.get_file_metadata("/todelete.txt") is None


def test_remove_path_nonexistent_is_noop(cache: HashCache) -> None:
    cache.remove_path("/never/existed.txt")  # must not raise


def test_remove_path_does_not_affect_other_entries(cache: HashCache) -> None:
    cache.store("/keep.txt", 1, 1.0, "keep", None, None)
    cache.store("/drop.txt", 2, 2.0, "drop", None, None)
    cache.remove_path("/drop.txt")
    result = cache.lookup("/keep.txt", 1, 1.0)
    assert result is not None and result[0] == "keep"


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_removes_missing_paths(cache: HashCache, tmp_path: Path) -> None:
    root = str(tmp_path)
    gone = str(tmp_path / "gone.txt")
    cache.store(gone, 1, 1.0, "h", None, None)
    cache.prune(root)
    assert cache.get_file_metadata(gone) is None


def test_prune_keeps_existing_paths(cache: HashCache, tmp_path: Path) -> None:
    root = str(tmp_path)
    present = tmp_path / "present.txt"
    present.write_text("hi")
    cache.store(str(present), 2, 1.0, "h", None, None)
    cache.prune(root)
    assert cache.get_file_metadata(str(present)) is not None


def test_prune_only_affects_entries_under_root(
    cache: HashCache, tmp_path: Path
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    # Store an entry under root_b that does NOT exist on disk
    gone_b = str(root_b / "gone.txt")
    cache.store(gone_b, 1, 1.0, "hb", None, None)
    # Prune root_a — should not touch root_b's entry
    cache.prune(str(root_a))
    assert cache.get_file_metadata(gone_b) is not None


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_stores_and_lookups(cache: HashCache) -> None:
    """Multiple threads writing and reading must not corrupt the cache."""
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            path = f"/thread/{index}.txt"
            h = f"hash{index}"
            cache.store(path, index, float(index), h, None, None)
            result = cache.lookup(path, index, float(index))
            assert result is not None and result[0] == h
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
