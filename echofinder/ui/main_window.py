from __future__ import annotations

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
from echofinder.ui.file_tree_view import FileTreeView
from echofinder.ui.metadata_panel import MetadataPanel
from echofinder.ui.preview_pane import PreviewPane
from echofinder.ui.tree_model import FileTreeModel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
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

        self._build_ui()
        self._connect_engine_signals()

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
        # --- Toolbar ---
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = toolbar.addAction("Open Folder\u2026")
        open_action.triggered.connect(self.select_root_folder)

        # --- Central widget: horizontal splitter ---
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self._main_splitter)

        # Left pane: file tree
        self._tree_view = FileTreeView()
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
        self._hashing_engine.hashing_started.connect(self._on_hashing_started)
        self._hashing_engine.progress_updated.connect(self._on_progress_updated)
        self._hashing_engine.hashing_complete.connect(self._on_hashing_complete)
        self._hashing_engine.hashing_cancelled.connect(self._on_hashing_cancelled)
        self._hashing_engine.file_hashed.connect(self._metadata_panel.on_file_hashed)

    # ------------------------------------------------------------------
    # Session restore
    # ------------------------------------------------------------------

    def _restore_session(self) -> None:
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
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Root Folder",
            str(self._session.get_root() or Path.home()),
        )
        if folder:
            self._set_root(Path(folder), restore_expansion=False)

    def _set_root(self, path: Path, *, restore_expansion: bool) -> None:
        # Cancel any in-progress hashing before switching roots
        if self._hashing_engine.isRunning():
            self._hashing_engine.cancel()
            self._hashing_engine.wait(5000)

        # Disconnect old model's signals before discarding it
        if self._tree_model is not None:
            try:
                self._tree_view.expanded.disconnect(self._save_expansion_state)
                self._tree_view.collapsed.disconnect(self._save_expansion_state)
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
        self._tree_view.collapsed.connect(self._save_expansion_state)
        self._tree_view.selectionModel().currentChanged.connect(
            self._on_selection_changed
        )

        # Show empty state (no file selected yet after changing root)
        self._preview_pane.show_empty()
        self._metadata_panel.clear()

        # Start background hashing for the new root
        self._hashing_engine.start_hashing(path)

    # ------------------------------------------------------------------
    # Hashing engine slots (called on main thread via queued connection)
    # ------------------------------------------------------------------

    def _on_hashing_started(self, total: int) -> None:
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
        if self._progress_bar is not None:
            self._progress_bar.setValue(current)
        if self._progress_label is not None:
            self._progress_label.setText(
                f"Hashing\u2026 {current:,} / {total:,} ({from_cache:,} from cache)"
            )

    def _on_hashing_complete(self) -> None:
        self._remove_progress_widgets()

    def _on_hashing_cancelled(self) -> None:
        self._remove_progress_widgets()

    def _remove_progress_widgets(self) -> None:
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
        if self._tree_model is None:
            return
        expanded: list[str] = []
        self._collect_expanded(QModelIndex(), expanded)
        self._session.set_expansion_state(expanded)

    def _collect_expanded(self, parent: QModelIndex, result: list[str]) -> None:
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

    def _on_file_selected(self, path: Path) -> None:
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
        if self._hashing_engine.isRunning():
            self._hashing_engine.cancel()
            self._hashing_engine.wait(5000)
        self._hash_cache.close()
        super().closeEvent(event)
