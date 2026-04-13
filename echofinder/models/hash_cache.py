from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from platformdirs import user_config_dir

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
    _APP_NAME = "echofinder"
    _DB_FILE = "cache.db"

    def __init__(self) -> None:
        db_dir = Path(user_config_dir(self._APP_NAME))
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / self._DB_FILE
        self._was_reset = False
        self._lock = threading.Lock()
        self._conn = self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA case_sensitive_like = ON")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            # Validate the DB is readable and well-formed
            conn.execute("SELECT COUNT(*) FROM files").fetchone()
            return conn
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            self._reset_db()
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA case_sensitive_like = ON")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            return conn

    def _reset_db(self) -> None:
        try:
            if self._db_path.exists():
                self._db_path.unlink()
        except OSError:
            pass
        self._was_reset = True

    @property
    def was_reset(self) -> bool:
        return self._was_reset

    def lookup(self, path: str, size: int, mtime: float) -> str | None:
        """Return cached hash if path, size, and mtime all match; else None."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT hash FROM files WHERE path=? AND size=? AND mtime=?",
                    (path, size, mtime),
                )
                row = cur.fetchone()
            return row[0] if row else None
        except sqlite3.Error:
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
        except sqlite3.Error:
            pass

    def prune(self, root: str) -> None:
        """Remove entries under *root* whose paths no longer exist on disk."""
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
        except sqlite3.Error:
            pass

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
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE files SET path=? WHERE path=?",
                    (new_path, old_path),
                )
                self._conn.commit()
        except sqlite3.Error:
            pass

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
        try:
            with self._lock:
                self._conn.close()
        except sqlite3.Error:
            pass
