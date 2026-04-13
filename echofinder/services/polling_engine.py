"""Live filesystem polling engine.

Polls the root directory every POLLING_INTERVAL_SECONDS to detect external
changes without user intervention.  Each cycle performs three ordered steps:

  1. Removals  — paths in the loaded tree that no longer exist on disk.
  2. Changes   — regular files whose size or mtime differs from the cached
                 values, queued for re-hashing.
  3. Additions — new paths found by walking the root that are not yet in the
                 loaded tree.

Architecture
------------
PollingEngine is a QThread subclass.  Its ``run()`` loop sleeps for
POLLING_INTERVAL_SECONDS using a threading.Event so the sleep can be
interrupted immediately when the root changes or the engine is stopped.

A ``_known_paths`` snapshot is maintained by the main thread via
``update_known_paths()``.  The polling thread reads a copy of this snapshot
at the start of each cycle under a threading.Lock so the read is safe across
threads.  The snapshot contains all paths currently loaded in the tree model
(both files and directories).

Signals are emitted on the polling thread and delivered to main-thread slots
via Qt's auto-connection (queued across threads).  All tree and cache
mutations happen on the main thread in response to these signals.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from echofinder.models.hash_cache import HashCache

# Polling interval: 30 seconds.  Chosen to balance responsiveness to external
# changes (e.g. another application creating or deleting files) against
# unnecessary I/O overhead on local filesystems.  Not user-configurable in v1.
POLLING_INTERVAL_SECONDS = 30


class PollingEngine(QThread):
    """Background thread that periodically checks the root for filesystem changes.

    Signals
    -------
    entries_removed(list[str])
        Paths that were in the loaded tree but no longer exist on disk.
        The main thread should remove them from the tree and the hash cache.

    entries_added(list[str])
        Paths found on disk that are not in the loaded tree.
        The main thread should refresh affected directories and queue hashing.

    entries_changed(list[str])
        Regular files whose size or mtime has changed since last hashed.
        The main thread should reset their slot-4 state to NOT_HASHED and
        queue them for re-hashing.
    """

    entries_removed = pyqtSignal(list)   # list[str]
    entries_added = pyqtSignal(list)     # list[str]
    entries_changed = pyqtSignal(list)   # list[str]

    def __init__(self, cache: HashCache, parent=None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._root: Path | None = None
        self._stopped = False

        # threading.Event used to interrupt the interval sleep when the root
        # changes or the engine is stopped.
        self._wake = threading.Event()

        # Thread-safe snapshot of paths currently loaded in the tree model.
        # Updated by the main thread via update_known_paths(); read by the
        # polling thread at the start of each cycle.
        self._paths_lock = threading.Lock()
        self._known_paths: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Main-thread API
    # ------------------------------------------------------------------

    def start_polling(self, root: Path) -> None:
        """Start or restart polling for *root*.

        Safe to call from the main thread at any time.  If the engine is
        already running, the current sleep is interrupted and the timer
        resets for the new root (no cycle runs for the old root after this
        call returns).
        """
        with self._paths_lock:
            self._root = root
        self._stopped = False
        self._wake.set()            # interrupt any in-progress sleep
        if not self.isRunning():
            self.start()

    def update_known_paths(self, paths: frozenset[str]) -> None:
        """Update the snapshot of paths loaded in the tree model.

        Called by the main thread whenever the tree is expanded or collapsed,
        or when a new root is set.  The polling thread reads this value at
        the start of each cycle.
        """
        with self._paths_lock:
            self._known_paths = paths

    def stop(self) -> None:
        """Stop polling permanently.  Safe to call from the main thread."""
        self._stopped = True
        self._wake.set()            # interrupt any in-progress sleep

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main loop: sleep → wake → cycle, repeat until stopped."""
        while not self._stopped:
            self._wake.clear()
            # Sleep for POLLING_INTERVAL_SECONDS or until woken.
            # wait() returns True if the event was set (interrupted early),
            # False if the timeout elapsed normally.
            interrupted = self._wake.wait(POLLING_INTERVAL_SECONDS)

            if self._stopped:
                break

            if interrupted:
                # Root changed or stop requested — reset the timer and wait again.
                # This ensures a full interval elapses after each root change
                # before the first cycle runs.
                continue

            with self._paths_lock:
                root = self._root
                known = self._known_paths

            if root is None:
                continue

            self._run_cycle(root, known)

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _run_cycle(self, root: Path, known_paths: frozenset[str]) -> None:
        """Execute one complete poll cycle for *root*.

        Checks for cancellation (root change or stop) between each step so
        that a root change during a cycle terminates quickly and cleanly.
        """
        # ----------------------------------------------------------------
        # Step 1 — Removals
        # ----------------------------------------------------------------
        # Check every loaded path for existence.  Anything missing is
        # collected and emitted; the main thread removes it from the tree
        # and the hash cache.
        removed: list[str] = []
        for path_str in known_paths:
            if self._should_abort():
                return
            try:
                if not os.path.exists(path_str):
                    removed.append(path_str)
            except OSError:
                removed.append(path_str)

        if removed:
            self.entries_removed.emit(removed)

        # ----------------------------------------------------------------
        # Step 2 — Changes
        # ----------------------------------------------------------------
        # For regular files still present, compare current stat against the
        # cached size and mtime.  A difference means the file has been
        # modified externally and needs re-hashing.
        removed_set = set(removed)
        changed: list[str] = []
        for path_str in known_paths:
            if self._should_abort():
                return
            if path_str in removed_set:
                continue
            try:
                # Skip directories and symlinks — only regular files are hashed
                if os.path.isdir(path_str) or os.path.islink(path_str):
                    continue
                stat = os.stat(path_str)
            except (PermissionError, OSError):
                continue  # permission errors skip this file and continue
            cached = self._cache.get_cached_stat(path_str)
            if cached is not None:
                cached_size, cached_mtime = cached
                if stat.st_size != cached_size or stat.st_mtime != cached_mtime:
                    changed.append(path_str)

        if changed:
            self.entries_changed.emit(changed)

        # ----------------------------------------------------------------
        # Step 3 — Additions
        # ----------------------------------------------------------------
        # Walk the root directory tree and collect paths not yet in the
        # loaded tree.  Uses the same onerror=ignore pattern as walk_files()
        # in scanner.py so inaccessible directories are silently skipped.
        added: list[str] = []
        try:
            for dirpath, dirnames, filenames in os.walk(
                str(root), followlinks=False, onerror=lambda _: None
            ):
                if self._should_abort():
                    return
                # New subdirectories
                for name in dirnames:
                    entry = os.path.join(dirpath, name)
                    if entry not in known_paths:
                        added.append(entry)
                # New files (includes symlinks to files)
                for name in filenames:
                    entry = os.path.join(dirpath, name)
                    if entry not in known_paths:
                        added.append(entry)
        except OSError:
            pass

        if added:
            self.entries_added.emit(added)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_abort(self) -> bool:
        """Return True if the current cycle should terminate early.

        Checked between polling steps and at the start of each inner loop
        iteration.  A True result means either the engine was stopped or the
        root directory changed; either way the in-progress cycle's results
        are stale and should not be emitted.
        """
        return self._stopped or self._wake.is_set()
