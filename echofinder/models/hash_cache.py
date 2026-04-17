from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

from platformdirs import user_config_dir

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS files (
        path     TEXT PRIMARY KEY,
        size     INTEGER NOT NULL,
        mtime    REAL NOT NULL,
        hash     TEXT,
        filetype TEXT,
        language TEXT
    )
"""


class HashCache:
    """Thread-safe SQLite cache mapping file paths to their SHA-256 hashes.

    Each row stores the path, file size, modification time, hash, MIME type,
    and detected programming language.  Cache hits require an exact match on
    all three of (path, size, mtime), so stale entries are automatically
    skipped when a file changes.

    All public methods acquire ``_lock`` so they are safe to call from any
    thread (the hashing engine's pool threads and the main thread both use
    this object concurrently).

    Attributes:
        _db_path: Filesystem path to the SQLite database file.
        _was_reset: ``True`` if the database was corrupted and recreated on
            startup; used to show a one-time warning to the user.
        _lock: Mutex protecting all SQLite operations.
        _conn: Active SQLite connection (``check_same_thread=False``).
    """

    _APP_NAME = "echofinder"
    _DB_FILE = "cache.db"

    def __init__(self) -> None:
        """Open (or create) the cache database in the user config directory."""
        db_dir = Path(user_config_dir(self._APP_NAME))
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / self._DB_FILE
        self._was_reset = False
        self._lock = threading.Lock()
        self._conn = self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        """Create the SQLite connection and ensure the schema exists.

        If the database file is corrupt or unreadable, it is deleted and
        recreated via ``_reset_db``.

        Returns:
            A ready-to-use ``sqlite3.Connection``.
        """
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA case_sensitive_like = ON")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            # Validate the DB is readable and well-formed
            conn.execute("SELECT COUNT(*) FROM files").fetchone()
            logger.debug("Cache database opened: %s", self._db_path)
            return conn
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            logger.warning("Cache corruption detected (%s); resetting database: %s", type(exc).__name__, exc)
            self._reset_db()
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA case_sensitive_like = ON")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            return conn

    def _reset_db(self) -> None:
        """Delete the on-disk database file and mark the cache as reset."""
        try:
            if self._db_path.exists():
                self._db_path.unlink()
                logger.info("Corrupted cache file deleted; fresh database will be created: %s", self._db_path)
        except OSError:
            pass
        self._was_reset = True

    @property
    def was_reset(self) -> bool:
        """``True`` if the database was corrupted and recreated on startup.

        Returns:
            Whether the cache was reset during ``__init__``.
        """
        return self._was_reset

    def lookup(self, path: str, size: int, mtime: float) -> tuple[str, str | None] | None:
        """Return (hash, filetype) if path, size, and mtime all match; else None."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT hash, filetype FROM files WHERE path=? AND size=? AND mtime=?",
                    (path, size, mtime),
                )
                row = cur.fetchone()
            if row:
                logger.debug("Cache hit: %s", path)
            return (row[0], row[1]) if row else None
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            logger.exception("Cache read error for %s", path)
            return None

    def store(
        self,
        path: str,
        size: int,
        mtime: float,
        hash_val: str,
        filetype: str | None,
        language: str | None,
    ) -> None:
        """Insert or replace a cache entry."""
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO files "
                    "(path, size, mtime, hash, filetype, language) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (path, size, mtime, hash_val, filetype, language),
                )
                self._conn.commit()
            logger.debug("Cache write: %s", path)
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            logger.exception("Cache write error for %s", path)

    def prune(self, root: str) -> None:
        """Remove entries under *root* whose paths no longer exist on disk."""
        logger.debug("Cache prune starting for root: %s", root)
        root_prefix = root.rstrip("/\\") + os.sep
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT path FROM files WHERE path LIKE ?",
                    (root_prefix + "%",),
                )
                candidates = [row[0] for row in cur.fetchall()]
            missing = [p for p in candidates if not os.path.exists(p)]
            if missing:
                with self._lock:
                    self._conn.executemany(
                        "DELETE FROM files WHERE path=?",
                        [(p,) for p in missing],
                    )
                    self._conn.commit()
            logger.info("Cache prune removed %d stale entries under %s", len(missing), root)
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            logger.exception("Cache prune error for root %s", root)

    def get_file_metadata(self, path: str) -> dict | None:
        """Return {hash, filetype, language} for *path* if cached; else None."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT hash, filetype, language FROM files WHERE path=?",
                    (path,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return {"hash": row[0], "filetype": row[1], "language": row[2]}
        except sqlite3.Error:
            return None

    def get_duplicate_paths(self, hash_val: str, exclude_path: str) -> list[str]:
        """Return all cached paths sharing *hash_val*, excluding *exclude_path*."""
        if not hash_val:
            return []
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT path FROM files WHERE hash=? AND path!=?",
                    (hash_val, exclude_path),
                )
                rows = cur.fetchall()
            return [row[0] for row in rows]
        except sqlite3.Error:
            return []

    def update_path(self, old_path: str, new_path: str) -> None:
        """Update a cache entry's path, preserving all other values (called by Stage 6)."""
        logger.debug("Cache update_path: %s → %s", old_path, new_path)
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE files SET path=? WHERE path=?",
                    (new_path, old_path),
                )
                self._conn.commit()
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            logger.exception("Cache update_path error: %s → %s", old_path, new_path)

    def get_cached_stat(self, path: str) -> tuple[int, float] | None:
        """Return the cached (size, mtime) for *path*, or None if not cached.

        Used by the polling engine to detect files that have changed on disk
        since they were last hashed.
        """
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT size, mtime FROM files WHERE path=?",
                    (path,),
                )
                row = cur.fetchone()
            return (row[0], row[1]) if row else None
        except sqlite3.Error:
            return None

    def remove_path(self, path: str) -> None:
        """Delete the cache entry for *path* (used when polling detects a removal)."""
        try:
            with self._lock:
                self._conn.execute("DELETE FROM files WHERE path=?", (path,))
                self._conn.commit()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        """Close the database connection; called on application shutdown."""
        try:
            with self._lock:
                self._conn.close()
        except sqlite3.Error:
            pass
