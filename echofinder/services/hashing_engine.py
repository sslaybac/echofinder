from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from PyQt6.QtCore import QRunnable, QThread, QThreadPool, pyqtSignal

from echofinder.models.hash_cache import HashCache
from echofinder.models.scanner import walk_files


# ---------------------------------------------------------------------------
# File-level helpers (run on pool threads — no Qt, no shared state)
# ---------------------------------------------------------------------------

def _compute_hash(path: str) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _detect_mime(path: str) -> str | None:
    try:
        import magic
        return magic.from_file(path, mime=True)
    except Exception:
        return None


def _detect_language(path: str) -> str | None:
    try:
        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound
        try:
            lexer = get_lexer_for_filename(path)
            return lexer.name
        except ClassNotFound:
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# QRunnable: one per file, executed on the global thread pool
# ---------------------------------------------------------------------------

class _HashTask(QRunnable):
    def __init__(
        self,
        path: str,
        cache: HashCache,
        on_done,        # callable(is_cache_hit, hash_val, filetype, language)
        is_cancelled,   # callable() -> bool
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._cache = cache
        self._on_done = on_done
        self._is_cancelled = is_cancelled

    def run(self) -> None:
        if self._is_cancelled():
            self._on_done(False, None, None, None)
            return

        path = self._path

        try:
            stat = os.stat(path)
        except OSError:
            self._on_done(False, None, None, None)
            return

        size = stat.st_size
        mtime = stat.st_mtime

        cached_hash = self._cache.lookup(path, size, mtime)
        if cached_hash is not None:
            self._on_done(True, cached_hash, None, None)
            return

        try:
            hash_val = _compute_hash(path)
        except PermissionError:
            self._on_done(False, None, None, None)
            return
        except OSError:
            self._on_done(False, None, None, None)
            return

        filetype = _detect_mime(path)
        language = _detect_language(path)

        self._cache.store(path, size, mtime, hash_val, filetype, language)
        self._on_done(False, hash_val, filetype, language)


# ---------------------------------------------------------------------------
# QThread coordinator: owns the walk, pool submission, and progress signals
# ---------------------------------------------------------------------------

class HashingEngine(QThread):
    # Emitted after walk_files completes; total is the number of paths to process
    hashing_started = pyqtSignal(int)
    # Emitted after each file is processed (cache hit or freshly hashed)
    progress_updated = pyqtSignal(int, int, int)   # current, total, from_cache
    # Emitted when all files have been processed successfully
    hashing_complete = pyqtSignal()
    # Emitted when the run was cancelled before completing
    hashing_cancelled = pyqtSignal()
    # Emitted for each file that was freshly hashed (not a cache hit)
    file_hashed = pyqtSignal(str, str, str, str)   # path, hash, filetype, language

    def __init__(self, cache: HashCache, parent=None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._root: Path | None = None
        self._cancelled = False
        self._lock = threading.Lock()

    def start_hashing(self, root: Path) -> None:
        """Set the root and start the background thread."""
        self._root = root
        self._cancelled = False
        self.start()

    def cancel(self) -> None:
        """Signal cancellation; in-flight tasks finish, queued tasks skip hashing."""
        self._cancelled = True

    def run(self) -> None:
        root = self._root
        if root is None:
            self.hashing_complete.emit()
            return

        # Walk on this background thread so the main thread is never blocked
        all_paths = list(walk_files(root))

        # Separate symlinks (excluded from queue per spec) from regular files
        symlinks: list[str] = []
        regular: list[str] = []
        for p in all_paths:
            s = str(p)
            if os.path.islink(s):
                symlinks.append(s)
            else:
                regular.append(s)

        total = len(symlinks) + len(regular)
        self.hashing_started.emit(total)

        if total == 0:
            self.hashing_complete.emit()
            return

        if self._cancelled:
            self.hashing_cancelled.emit()
            return

        # Prune stale cache entries for this root before hashing begins
        self._cache.prune(str(root))

        pool = QThreadPool.globalInstance()
        pool.setMaxThreadCount(max(1, (os.cpu_count() or 2) - 1))

        # Symlinks are pre-counted: they advance current without producing a hash
        counter = [len(symlinks), 0]  # [current, from_cache]
        semaphore = threading.Semaphore(0)

        self.progress_updated.emit(counter[0], total, counter[1])

        submitted = 0
        for path_str in regular:
            if self._cancelled:
                break

            def make_callback(p: str):
                def on_done(
                    is_cache_hit: bool,
                    hash_val: str | None,
                    ft: str | None,
                    lang: str | None,
                ) -> None:
                    with self._lock:
                        counter[0] += 1
                        if is_cache_hit:
                            counter[1] += 1
                        cur, cache_hits = counter[0], counter[1]
                    self.progress_updated.emit(cur, total, cache_hits)
                    if hash_val:
                        self.file_hashed.emit(p, hash_val, ft or "", lang or "")
                    semaphore.release()
                return on_done

            task = _HashTask(
                path_str,
                self._cache,
                make_callback(path_str),
                lambda: self._cancelled,
            )
            pool.start(task)
            submitted += 1

        for _ in range(submitted):
            semaphore.acquire()

        if self._cancelled:
            self.hashing_cancelled.emit()
        else:
            self.hashing_complete.emit()
