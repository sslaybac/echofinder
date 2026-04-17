from __future__ import annotations

import hashlib
import logging
import os
import queue as _queue
import threading
from pathlib import Path

from PyQt6.QtCore import QRunnable, QThread, QThreadPool, QTimer, pyqtSignal

from echofinder.models.hash_cache import HashCache
from echofinder.models.scanner import walk_files

logger = logging.getLogger(__name__)

# Drain timer interval: how often the main thread processes queued hash results.
_DRAIN_INTERVAL_MS = 100
# Log a warning when a single drain tick finds more than this many queued items.
_BACKLOG_WARN_THRESHOLD = 1000

# Sentinels pushed onto the result queue to signal end-of-run state.
# Always compare with `is`, never `==`.
_SENTINEL_COMPLETE = object()
_SENTINEL_CANCELLED = object()


# ---------------------------------------------------------------------------
# File-level helpers (run on pool threads — no Qt, no shared state)
# ---------------------------------------------------------------------------

def _compute_hash(path: str) -> str:
    """Compute the SHA-256 hex digest of a file using 64 KB read chunks.

    Args:
        path: Absolute path string to the file to hash.

    Returns:
        Lowercase hex-encoded SHA-256 digest string.

    Raises:
        OSError: If the file cannot be opened or read.
    """
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _detect_mime(path: str) -> str | None:
    """Return the MIME type of *path* via libmagic, or ``None`` on failure.

    Args:
        path: Absolute path string to inspect.

    Returns:
        A MIME type string such as ``'image/png'``, or ``None`` if libmagic
        is unavailable or raises an exception.
    """
    try:
        import magic
        result = magic.from_file(path, mime=True)
        if result is None:
            logger.debug("python-magic returned None for %s", path)
        return result
    except Exception as exc:
        logger.debug("python-magic raised exception for %s: %s", path, exc)
        return None


def _detect_language(path: str) -> str | None:
    """Return the Pygments lexer name for *path* based on its filename, or ``None``.

    Args:
        path: Absolute path string used for filename-based lexer lookup.

    Returns:
        A human-readable language name such as ``'Python'``, or ``None`` if
        no lexer matches or Pygments is unavailable.
    """
    try:
        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound
        try:
            lexer = get_lexer_for_filename(path)
            return lexer.name
        except ClassNotFound:
            logger.debug("Pygments found no language match for %s", path)
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# QRunnable: one per file, executed on the global thread pool
# ---------------------------------------------------------------------------

class _HashTask(QRunnable):
    """Per-file hashing task executed on the global Qt thread pool.

    Checks the ``HashCache`` before computing a hash so that unmodified files
    are returned instantly without re-reading from disk.  Results are delivered
    asynchronously via the ``on_done`` callback.
    """

    def __init__(
        self,
        path: str,
        cache: HashCache,
        on_done,        # callable(is_cache_hit, hash_val, filetype, language)
        is_cancelled,   # callable() -> bool
    ) -> None:
        """Initialise the task.

        Args:
            path: Absolute path string of the file to hash.
            cache: Shared ``HashCache`` instance for lookup and storage.
            on_done: Callback invoked with
                ``(is_cache_hit: bool, hash_val: str | None,
                filetype: str | None, language: str | None)``
                when the task completes (success or failure).
            is_cancelled: Zero-argument callable returning ``True`` if the
                owning ``HashingEngine`` has been cancelled.
        """
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._cache = cache
        self._on_done = on_done
        self._is_cancelled = is_cancelled

    def run(self) -> None:
        """Execute the hash task on a pool thread.

        Checks cancellation first, then attempts a cache lookup.  On a cache
        miss, computes the SHA-256 hash, detects MIME type and language, stores
        the result in the cache, and invokes ``on_done``.  Any I/O error
        results in ``on_done(False, None, None, None)``.
        """
        try:
            self._run_impl()
        except Exception:
            logger.exception("Unexpected error in hash task for %s", self._path)
            self._on_done(False, None, None, None)

    def _run_impl(self) -> None:
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

        cached = self._cache.lookup(path, size, mtime)
        if cached is not None:
            cached_hash, cached_filetype = cached
            self._on_done(True, cached_hash, cached_filetype, None)
            return

        try:
            hash_val = _compute_hash(path)
        except PermissionError:
            logger.debug("Skipped (PermissionError): %s", path)
            self._on_done(False, None, None, None)
            return
        except OSError:
            self._on_done(False, None, None, None)
            return

        filetype = _detect_mime(path)
        language = _detect_language(path)

        self._cache.store(path, size, mtime, hash_val, filetype, language)
        logger.debug("Hashed successfully: %s", path)
        self._on_done(False, hash_val, filetype, language)


# ---------------------------------------------------------------------------
# QThread coordinator: owns the walk, pool submission, and progress signals
# ---------------------------------------------------------------------------

class HashingEngine(QThread):
    # Emitted after walk_files completes; total is the number of paths to process
    hashing_started = pyqtSignal(int)
    # Emitted after each file is processed (cache hit or freshly hashed)
    progress_updated = pyqtSignal(int, int, int, str)   # current, total, from_cache, filename
    # Emitted when all files have been processed successfully
    hashing_complete = pyqtSignal()
    # Emitted when the run was cancelled before completing
    hashing_cancelled = pyqtSignal()
    # Emitted for each file that was freshly hashed (not a cache hit)
    file_hashed = pyqtSignal(str, str, str, str)   # path, hash, filetype, language

    def __init__(self, cache: HashCache, parent=None) -> None:
        """Initialise the hashing engine without starting it.

        Args:
            cache: Shared ``HashCache`` used for lookup and persistence.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._cache = cache
        self._root: Path | None = None
        self._cancelled = False
        self._lock = threading.Lock()

        # Pool thread callbacks push (path, hash, ft, lang) tuples or sentinel
        # objects here instead of emitting signals directly.  The drain timer
        # processes the queue on the main thread every _DRAIN_INTERVAL_MS ms,
        # collapsing thousands of per-file signal emissions into at most ~10/s.
        self._result_queue: _queue.SimpleQueue = _queue.SimpleQueue()

        # Latest progress snapshot written by pool callbacks under _progress_lock;
        # read and cleared by _drain_results().  None means no update pending.
        self._pending_progress: tuple[int, int, int, str] | None = None
        self._progress_lock = threading.Lock()

        # Per-run diagnostics reset in start_hashing().
        self._drain_cycles = 0
        self._peak_batch = 0

        # QThread objects are owned by their creator thread (main thread here),
        # so this timer fires on the main thread even though HashingEngine is a
        # QThread.  It runs continuously; _drain_results() is a no-op on an
        # empty queue so the idle overhead is negligible.
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(_DRAIN_INTERVAL_MS)
        self._drain_timer.timeout.connect(self._drain_results)
        self._drain_timer.start()

    def start_hashing(self, root: Path) -> None:
        """Set the root and start the background thread."""
        self._root = root
        self._cancelled = False
        self._drain_cycles = 0
        self._peak_batch = 0
        self.start()

    def cancel(self) -> None:
        """Signal cancellation; in-flight tasks finish, queued tasks skip hashing."""
        self._cancelled = True

    def run(self) -> None:
        """QThread entry point: walk *root*, submit pool tasks, await completion.

        Performs the directory walk on this background thread so the main
        thread is never blocked.  Symlinks are pre-counted and advance the
        progress counter without generating a hash.  Emits ``hashing_started``
        once the total count is known, then ``hashing_complete`` or
        ``hashing_cancelled`` when all tasks have finished.
        """
        root = self._root
        if root is None:
            self._result_queue.put(_SENTINEL_COMPLETE)
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
                logger.debug("Skipped (symlink): %s", s)
            else:
                regular.append(s)

        total = len(symlinks) + len(regular)
        logger.info("Hashing run started: root=%s, total=%d files", root, total)
        self.hashing_started.emit(total)

        if total == 0:
            logger.info("Hashing run completed: 0 files to process")
            self._result_queue.put(_SENTINEL_COMPLETE)
            return

        if self._cancelled:
            logger.info("Hashing run cancelled before submission (root changed)")
            self._result_queue.put(_SENTINEL_CANCELLED)
            return

        # Prune stale cache entries for this root before hashing begins
        self._cache.prune(str(root))

        pool = QThreadPool.globalInstance()
        pool.setMaxThreadCount(max(1, (os.cpu_count() or 2) - 1))

        # Symlinks are pre-counted: they advance current without producing a hash.
        # Seed pending_progress so the first drain tick shows the symlink offset.
        counter = [len(symlinks), 0]  # [current, from_cache]
        semaphore = threading.Semaphore(0)

        with self._progress_lock:
            self._pending_progress = (counter[0], total, counter[1], "")

        submitted = 0
        for path_str in regular:
            if self._cancelled:
                break

            logger.debug("Submitted to hash queue: %s", path_str)

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
                    # Write progress snapshot; drain timer emits at most once per tick.
                    with self._progress_lock:
                        self._pending_progress = (cur, total, cache_hits, p)
                    # Push result onto queue; drain timer emits file_hashed in batch.
                    self._result_queue.put((p, hash_val or "", ft or "", lang or ""))
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
            logger.info("Hashing run cancelled (root changed mid-run)")
            self._result_queue.put(_SENTINEL_CANCELLED)
        else:
            cache_hits = counter[1]
            actively_hashed = submitted - cache_hits
            logger.info(
                "Hashing run completed: processed=%d, cache_hits=%d, hashed=%d",
                counter[0], cache_hits, actively_hashed,
            )
            self._result_queue.put(_SENTINEL_COMPLETE)

    def _drain_results(self) -> None:
        """Drain the result queue and emit signals on the main thread.

        Called by the drain timer every _DRAIN_INTERVAL_MS.  Processes all
        items queued since the last tick in one batch, emits file_hashed for
        each result with a valid hash, emits at most one progress_updated, then
        emits the terminal signal (hashing_complete or hashing_cancelled) after
        all results are delivered.
        """
        batch = []
        while True:
            try:
                batch.append(self._result_queue.get_nowait())
            except _queue.Empty:
                break

        n = len(batch)
        if n == 0:
            return

        self._drain_cycles += 1
        if n > self._peak_batch:
            self._peak_batch = n

        if n > _BACKLOG_WARN_THRESHOLD:
            logger.warning(
                "Result queue backlog: %d items pending — consider reducing drain interval", n
            )
        else:
            logger.debug("Drained %d result(s) in tick %d", n, self._drain_cycles)

        terminal = None
        for item in batch:
            if item is _SENTINEL_COMPLETE or item is _SENTINEL_CANCELLED:
                terminal = item
                continue
            path, hash_val, ft, lang = item
            if hash_val:
                self.file_hashed.emit(path, hash_val, ft, lang)

        # Emit at most one progress update per drain tick (last known state)
        with self._progress_lock:
            prog = self._pending_progress
            self._pending_progress = None
        if prog is not None:
            self.progress_updated.emit(*prog)

        # Terminal signal is emitted after all results and progress so that
        # connected slots (e.g. removing the progress bar) see a fully updated state.
        if terminal is _SENTINEL_COMPLETE:
            logger.info(
                "Hashing complete signal delivered: drain_cycles=%d, peak_batch=%d",
                self._drain_cycles, self._peak_batch,
            )
            self.hashing_complete.emit()
        elif terminal is _SENTINEL_CANCELLED:
            self.hashing_cancelled.emit()

    def rehash_paths(self, paths: list[str]) -> None:
        """Submit specific files for re-hashing via the global thread pool.

        Used by the polling engine after it detects that a file's size or mtime
        has changed since it was last hashed.  Each file is submitted as an
        independent task; progress tracking is not updated (these are minor
        incremental re-hashes, not a full scan).  Results are queued and
        delivered via the same drain timer as the bulk hashing run.
        """
        pool = QThreadPool.globalInstance()
        for path_str in paths:
            def make_callback(p: str):
                def on_done(
                    is_cache_hit: bool,
                    hash_val: str | None,
                    ft: str | None,
                    lang: str | None,
                ) -> None:
                    self._result_queue.put((p, hash_val or "", ft or "", lang or ""))
                return on_done

            task = _HashTask(
                path_str,
                self._cache,
                make_callback(path_str),
                lambda: False,  # individual rehash tasks are never cancelled mid-run
            )
            pool.start(task)
