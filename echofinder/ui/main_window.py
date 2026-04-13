from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QToolBar,
)

from echofinder.models.hash_cache import HashCache
from echofinder.models.session import SessionState
from echofinder.services.file_type import FileTypeResolver
from echofinder.services.hashing_engine import HashingEngine
from echofinder.services.polling_engine import PollingEngine
from echofinder.ui.file_tree_view import FileTreeView
from echofinder.ui.metadata_panel import MetadataPanel
from echofinder.ui.preview_pane import PreviewPane
from echofinder.ui.tree_model import FileTreeModel


class MainWindow(QMainWindow):
    """Top-level application window.

    Owns the toolbar, the main horizontal splitter (file tree | preview/metadata),
    the hashing engine, the polling engine, and the session state.  All signal
    wiring between the UI and the background services happens here.
    """

    def __init__(self) -> None:
        """Construct the window, engines, and UI; then restore the last session."""
        super().__init__()
        self.setWindowTitle("Echofinder")
        self.resize(1100, 700)

        self._session = SessionState()
        self._resolver = FileTypeResolver()
        self._tree_model: FileTreeModel | None = None

        # Hashing infrastructure
        self._hash_cache = HashCache()
        self._hashing_engine = HashingEngine(self._hash_cache)
        self._progress_bar: QProgressBar | None = None
        self._progress_label: QLabel | None = None

        # Polling infrastructure (Stage 7)
        self._polling_engine = PollingEngine(self._hash_cache)

        self._build_ui()
        self._connect_engine_signals()
        self._connect_polling_signals()

        # Warn the user if the cache was corrupted and reset on startup
        if self._hash_cache.was_reset:
            self.statusBar().showMessage(
                "Hash cache was corrupted and has been reset. Files will be re-hashed.",
                8000,
            )

        self._restore_session()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the toolbar, splitters, tree view, preview pane, and metadata panel."""
        # --- Toolbar ---
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = toolbar.addAction("Open Folder\u2026")
        open_action.triggered.connect(self.select_root_folder)

        toolbar.addSeparator()
        self._delete_action = toolbar.addAction("Delete")
        self._delete_action.setEnabled(False)
        self._delete_action.triggered.connect(self._on_toolbar_delete)

        # --- Central widget: horizontal splitter ---
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self._main_splitter)

        # Left pane: file tree
        self._tree_view = FileTreeView()
        self._tree_view.set_cache(self._hash_cache)
        self._tree_view.status_message.connect(lambda msg: self.statusBar().showMessage(msg))
        self._tree_view.status_clear.connect(self.statusBar().clearMessage)
        self._main_splitter.addWidget(self._tree_view)

        # Right pane: vertical splitter (preview + metadata)
        self._right_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.addWidget(self._right_splitter)

        # Preview pane (QStackedWidget containing all content widgets)
        self._preview_pane = PreviewPane()
        self._right_splitter.addWidget(self._preview_pane)

        # Metadata panel (Stage 5)
        self._metadata_panel = MetadataPanel(self._hash_cache)
        self._metadata_panel.setMinimumHeight(80)
        self._metadata_panel.setMaximumHeight(200)
        self._right_splitter.addWidget(self._metadata_panel)

        # Splitter proportions: tree 30 %, right panel 70 %
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 7)
        # Right splitter: preview 75 %, metadata 25 %
        self._right_splitter.setStretchFactor(0, 3)
        self._right_splitter.setStretchFactor(1, 1)

        # --- Status bar ---
        self.setStatusBar(QStatusBar())

        # --- Signal connections ---
        self._preview_pane.select_folder_requested.connect(self.select_root_folder)
        self._preview_pane.navigate_to_path.connect(self._on_navigate_to_path)
        self._preview_pane.encoding_detected.connect(self._metadata_panel.set_encoding)
        self._tree_view.file_selected.connect(self._on_file_selected)
        self._metadata_panel.navigate_to_path.connect(self._on_navigate_to_path)

    # ------------------------------------------------------------------
    # Hashing engine signal connections
    # ------------------------------------------------------------------

    def _connect_engine_signals(self) -> None:
        """Wire hashing engine signals to their main-thread handler slots."""
        self._hashing_engine.hashing_started.connect(self._on_hashing_started)
        self._hashing_engine.progress_updated.connect(self._on_progress_updated)
        self._hashing_engine.hashing_complete.connect(self._on_hashing_complete)
        self._hashing_engine.hashing_cancelled.connect(self._on_hashing_cancelled)
        self._hashing_engine.file_hashed.connect(self._metadata_panel.on_file_hashed)

    # ------------------------------------------------------------------
    # Polling engine signal connections
    # ------------------------------------------------------------------

    def _connect_polling_signals(self) -> None:
        """Wire polling engine signals to their main-thread handler slots."""
        self._polling_engine.entries_removed.connect(self._on_entries_removed)
        self._polling_engine.entries_added.connect(self._on_entries_added)
        self._polling_engine.entries_changed.connect(self._on_entries_changed)

    # ------------------------------------------------------------------
    # Polling engine slots (called on main thread via queued connection)
    # ------------------------------------------------------------------

    def _on_entries_removed(self, paths: list) -> None:
        """Remove paths from the tree and hash cache after polling detects deletion."""
        if self._tree_model is None:
            return
        # Snapshot selection and expansion before refresh_dir removes rows.
        # refresh_dir does beginRemoveRows/endRemoveRows which clears the Qt
        # selection if the selected item is in the refreshed directory, and
        # collapses everything when the refreshed directory is the root.
        selected_path = self._selected_path()
        expanded = self._tree_view._collect_all_expanded()

        self._tree_model.on_entries_removed(paths)
        # Prune deleted paths from the persistent hash cache.
        for path_str in paths:
            self._hash_cache.remove_path(path_str)

        self._tree_view._restore_all_expanded(expanded)
        self._restore_selected_path(selected_path, skip_paths=set(paths))
        self._refresh_polling_snapshot()

    def _on_entries_added(self, paths: list) -> None:
        """Refresh affected directories and queue new files for hashing."""
        if self._tree_model is None:
            return
        # Snapshot selection and expansion before refresh_dir removes rows.
        # Same issue as _on_entries_removed: rows are removed and re-inserted,
        # which clears Qt's selection and expansion tracking for that subtree.
        selected_path = self._selected_path()
        expanded = self._tree_view._collect_all_expanded()

        # Refresh every parent directory that is currently loaded so the new
        # entries appear in the tree.  refresh_dir() is a no-op for unloaded
        # parents; those entries will appear naturally when the user expands.
        parents: set[Path] = set()
        new_files: list[str] = []
        for path_str in paths:
            parents.add(Path(path_str).parent)
            # Queue regular files (not dirs or symlinks) for hashing.
            try:
                if not os.path.isdir(path_str) and not os.path.islink(path_str):
                    new_files.append(path_str)
            except OSError:
                pass
        for parent in parents:
            self._tree_model.refresh_dir(parent)

        self._tree_view._restore_all_expanded(expanded)
        self._restore_selected_path(selected_path)
        if new_files:
            self._hashing_engine.rehash_paths(new_files)
        self._refresh_polling_snapshot()

    def _on_entries_changed(self, paths: list) -> None:
        """Reset hash state for changed files and queue them for re-hashing."""
        if self._tree_model is None:
            return
        self._tree_model.on_entries_changed(paths)
        self._hashing_engine.rehash_paths(list(paths))

    def _selected_path(self) -> Path | None:
        """Return the path of the currently selected tree item, or None."""
        idx = self._tree_view.currentIndex()
        if idx.isValid():
            node = idx.internalPointer()
            return node.path
        return None

    def _restore_selected_path(
        self, path: Path | None, skip_paths: set[str] | None = None
    ) -> None:
        """Re-select *path* in the tree after a refresh, if it still exists.

        *skip_paths* is an optional set of path strings to treat as gone
        (used after deletions so we do not try to re-select a removed item).
        """
        if path is None or self._tree_model is None:
            return
        if skip_paths and str(path) in skip_paths:
            return
        idx = self._tree_model.index_for_path(path)
        if idx.isValid():
            self._tree_view.setCurrentIndex(idx)

    def _refresh_polling_snapshot(self) -> None:
        """Push the current loaded-path snapshot to the polling engine."""
        if self._tree_model is not None:
            self._polling_engine.update_known_paths(
                self._tree_model.get_polling_snapshot()
            )

    # ------------------------------------------------------------------
    # Session restore
    # ------------------------------------------------------------------

    def _restore_session(self) -> None:
        """Restore the last root directory from session state, if it still exists."""
        root_str = self._session.get_root()
        if root_str:
            root = Path(root_str)
            if root.is_dir():
                self._set_root(root, restore_expansion=True)
                return
        self._preview_pane.show_empty()

    # ------------------------------------------------------------------
    # Root selection
    # ------------------------------------------------------------------

    def select_root_folder(self) -> None:
        """Open a directory picker dialog and set the chosen path as the new root."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Root Folder",
            str(self._session.get_root() or Path.home()),
        )
        if folder:
            self._set_root(Path(folder), restore_expansion=False)

    def _set_root(self, path: Path, *, restore_expansion: bool) -> None:
        """Switch to *path* as the new tree root.

        Cancels any in-flight hashing and polling for the previous root,
        builds a new ``FileTreeModel``, wires all signals, persists the new
        root to session state, and starts background hashing and polling.

        Args:
            path: Absolute ``Path`` to the new root directory.
            restore_expansion: When ``True``, re-expand folders saved in
                session state; when ``False``, clear the expansion state.
        """
        # Interrupt any in-progress polling cycle for the old root.
        # start_polling() at the end of this method will restart the cycle for
        # the new root.  We do NOT call stop() here because stop() terminates
        # the thread permanently; start_polling() uses _wake.set() to abort any
        # in-progress cycle and reset the interval, which is all we need.

        # Cancel any in-progress hashing before switching roots
        if self._hashing_engine.isRunning():
            self._hashing_engine.cancel()
            self._hashing_engine.wait(5000)

        # Disconnect old model's signals before discarding it
        if self._tree_model is not None:
            try:
                self._tree_view.expanded.disconnect(self._save_expansion_state)
                self._tree_view.expanded.disconnect(self._refresh_polling_snapshot)
                self._tree_view.collapsed.disconnect(self._save_expansion_state)
                self._tree_view.collapsed.disconnect(self._refresh_polling_snapshot)
                self._tree_view.selectionModel().currentChanged.disconnect(
                    self._on_selection_changed
                )
            except RuntimeError:
                pass
            try:
                self._hashing_engine.file_hashed.disconnect(self._tree_model.on_file_hashed)
            except (RuntimeError, TypeError):
                pass

        # Build new model and wire it to the hashing engine
        self._tree_model = FileTreeModel(self._resolver)
        self._hashing_engine.file_hashed.connect(self._tree_model.on_file_hashed)
        self._tree_model.set_root(path)
        self._tree_view.setModel(self._tree_model)

        # Persist new root; clear expansion if changing root
        self._session.set_root(str(path))
        if not restore_expansion:
            self._session.clear_expansion_state()

        self.setWindowTitle(f"Echofinder \u2014 {path}")

        # Restore or reset expansion state
        if restore_expansion:
            self._restore_expansion_state()

        # Connect signals after model is set
        self._tree_view.expanded.connect(self._save_expansion_state)
        self._tree_view.expanded.connect(self._refresh_polling_snapshot)
        self._tree_view.collapsed.connect(self._save_expansion_state)
        self._tree_view.collapsed.connect(self._refresh_polling_snapshot)
        self._tree_view.selectionModel().currentChanged.connect(
            self._on_selection_changed
        )

        # Show empty state (no file selected yet after changing root)
        self._preview_pane.show_empty()
        self._metadata_panel.clear()

        # Start background hashing for the new root
        self._hashing_engine.start_hashing(path)

        # Start the polling cycle for the new root.  Provide an initial
        # snapshot of loaded paths (just the root's eager-loaded children).
        self._polling_engine.update_known_paths(
            self._tree_model.get_polling_snapshot()
        )
        self._polling_engine.start_polling(path)

    # ------------------------------------------------------------------
    # Hashing engine slots (called on main thread via queued connection)
    # ------------------------------------------------------------------

    def _on_hashing_started(self, total: int) -> None:
        """Add progress bar and label to the status bar when hashing begins.

        Args:
            total: Total number of files to process (0 means nothing to do).
        """
        if total == 0:
            return
        self.statusBar().clearMessage()
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedWidth(200)
        self._progress_label = QLabel(f"Hashing\u2026 0 / {total:,} (0 from cache)")
        self.statusBar().addWidget(self._progress_bar)
        self.statusBar().addWidget(self._progress_label)

    def _on_progress_updated(self, current: int, total: int, from_cache: int) -> None:
        """Update the progress bar value and label text.

        Args:
            current: Number of files processed so far.
            total: Total number of files.
            from_cache: Number of files whose hash came from the cache.
        """
        if self._progress_bar is not None:
            self._progress_bar.setValue(current)
        if self._progress_label is not None:
            self._progress_label.setText(
                f"Hashing\u2026 {current:,} / {total:,} ({from_cache:,} from cache)"
            )

    def _on_hashing_complete(self) -> None:
        """Remove progress widgets from the status bar when hashing finishes."""
        self._remove_progress_widgets()

    def _on_hashing_cancelled(self) -> None:
        """Remove progress widgets from the status bar when hashing is cancelled."""
        self._remove_progress_widgets()

    def _remove_progress_widgets(self) -> None:
        """Remove and delete the progress bar and label from the status bar."""
        if self._progress_bar is not None:
            self.statusBar().removeWidget(self._progress_bar)
            self._progress_bar.deleteLater()
            self._progress_bar = None
        if self._progress_label is not None:
            self.statusBar().removeWidget(self._progress_label)
            self._progress_label.deleteLater()
            self._progress_label = None

    # ------------------------------------------------------------------
    # Expansion state persistence
    # ------------------------------------------------------------------

    def _save_expansion_state(self) -> None:
        """Collect expanded paths from the tree and persist them to session state."""
        if self._tree_model is None:
            return
        expanded: list[str] = []
        self._collect_expanded(QModelIndex(), expanded)
        self._session.set_expansion_state(expanded)

    def _collect_expanded(self, parent: QModelIndex, result: list[str]) -> None:
        """Recursively append expanded folder path strings to *result*.

        Args:
            parent: Model index to iterate from; invalid means the root.
            result: Accumulator list that receives expanded path strings.
        """
        model = self._tree_model
        if model is None:
            return
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            if self._tree_view.isExpanded(index):
                node = index.internalPointer()
                result.append(str(node.path))
                self._collect_expanded(index, result)

    def _restore_expansion_state(self) -> None:
        """Expand tree nodes for paths saved in session state, shallowest-first."""
        if self._tree_model is None:
            return
        paths = self._session.get_expansion_state()
        for path_str in sorted(paths, key=lambda p: len(Path(p).parts)):
            idx = self._tree_model.index_for_path(Path(path_str))
            if idx.isValid():
                self._tree_view.expand(idx)

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------

    def _on_selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        """Update the preview pane, metadata panel, and delete button on selection change.

        Args:
            current: The newly selected model index.
            _previous: The previously selected index (unused).
        """
        self._delete_action.setEnabled(current.isValid())
        if not current.isValid():
            self._preview_pane.show_empty()
            self._metadata_panel.clear()
            if self._tree_model is not None:
                self._tree_model.set_active_file(None)
            return

        node = current.internalPointer()
        root = self._tree_model.root_path() if self._tree_model else None

        # show_for_node emits encoding_detected (synchronously), which updates
        # the metadata panel's encoding row before display_file() is called.
        self._preview_pane.show_for_node(node, root)

        # Show metadata only for regular files; hide for folders and symlinks.
        is_file = not node.is_dir and not node.is_symlink
        if is_file:
            self._metadata_panel.display_file(str(node.path))
        else:
            self._metadata_panel.clear()

        # Notify model so it can promote matching nodes to DUPLICATE_SPECIFIC.
        if self._tree_model is not None:
            if is_file:
                self._tree_model.set_active_file(str(node.path))
            else:
                self._tree_model.set_active_file(None)

    def _on_toolbar_delete(self) -> None:
        """Trigger a delete on the currently selected tree item from the toolbar."""
        idx = self._tree_view.currentIndex()
        if idx.isValid():
            self._tree_view._trigger_delete(idx)

    def _on_file_selected(self, path: Path) -> None:
        """Handle the Enter-key ``file_selected`` signal from the tree view.

        The preview pane is already updated via ``currentChanged``, so this
        slot is intentionally a no-op.

        Args:
            path: The path of the selected file (unused here).
        """
        # Enter key on a file — preview pane already updated via currentChanged
        pass

    def _on_navigate_to_path(self, path_obj: object) -> None:
        """Navigate the tree to *path_obj* (a Path), expanding the parent if needed."""
        if self._tree_model is None:
            return
        path = path_obj if isinstance(path_obj, Path) else Path(str(path_obj))
        idx = self._tree_model.index_for_path(path)
        if not idx.isValid():
            return
        # Expand the parent folder if it is currently collapsed
        parent_idx = idx.parent()
        if parent_idx.isValid() and not self._tree_view.isExpanded(parent_idx):
            self._tree_view.expand(parent_idx)
        self._tree_view.setCurrentIndex(idx)
        self._tree_view.scrollTo(idx)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Stop background threads and close the hash cache on application exit."""
        # Stop the polling engine first so no signals fire after shutdown.
        if self._polling_engine.isRunning():
            self._polling_engine.stop()
            self._polling_engine.wait(5000)
        if self._hashing_engine.isRunning():
            self._hashing_engine.cancel()
            self._hashing_engine.wait(5000)
        self._hash_cache.close()
        super().closeEvent(event)
