from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QWidget,
)

from echofinder.models.file_node import FileType
from echofinder.models.hash_cache import HashCache
from echofinder.models.session import SessionState
from echofinder.services.file_type import FileTypeResolver
from echofinder.services.hashing_engine import HashingEngine
from echofinder.ui.empty_state import EmptyStateWidget
from echofinder.ui.file_tree_view import FileTreeView
from echofinder.ui.preview.folder_contents_widget import FolderContentsWidget
from echofinder.ui.preview.image_preview_widget import ImagePreviewWidget
from echofinder.ui.preview.media_playback_widget import MediaPlaybackWidget
from echofinder.ui.preview.pdf_preview_widget import PdfPreviewWidget
from echofinder.ui.preview.symlink_widget import SymlinkWidget
from echofinder.ui.preview.text_preview_widget import TextPreviewWidget
from echofinder.ui.preview.unreadable_widget import UnreadableFileWidget
from echofinder.ui.preview.unsupported_widget import UnsupportedFileWidget
from echofinder.ui.tree_model import FileTreeModel

# QStackedWidget slot indices for the preview pane
_PREVIEW_EMPTY = 0
_PREVIEW_SYMLINK = 1
_PREVIEW_FOLDER = 2
_PREVIEW_IMAGE = 3
_PREVIEW_TEXT = 4
_PREVIEW_PDF = 5
_PREVIEW_MEDIA = 6
_PREVIEW_UNSUPPORTED = 7
_PREVIEW_UNREADABLE = 8


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

        # Currently previewed path — used for load_failed messages
        self._current_preview_path: Path | None = None

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

        open_action = toolbar.addAction("Open Folder…")
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

        # Preview area: QStackedWidget
        self._preview_stack = QStackedWidget()
        self._right_splitter.addWidget(self._preview_stack)

        # Slot 0: empty state (unchanged from Stage 1)
        self._empty_state = EmptyStateWidget()
        self._preview_stack.addWidget(self._empty_state)          # index 0

        # Slot 1: symlink preview
        self._symlink_widget = SymlinkWidget()
        self._preview_stack.addWidget(self._symlink_widget)        # index 1

        # Slot 2: folder contents
        self._folder_widget = FolderContentsWidget()
        self._preview_stack.addWidget(self._folder_widget)         # index 2

        # Slot 3: image preview
        self._image_widget = ImagePreviewWidget()
        self._preview_stack.addWidget(self._image_widget)          # index 3

        # Slot 4: text / code preview
        self._text_widget = TextPreviewWidget()
        self._preview_stack.addWidget(self._text_widget)           # index 4

        # Slot 5: PDF preview
        self._pdf_widget = PdfPreviewWidget()
        self._preview_stack.addWidget(self._pdf_widget)            # index 5

        # Slot 6: media playback (video / audio)
        self._media_widget = MediaPlaybackWidget()
        self._preview_stack.addWidget(self._media_widget)          # index 6

        # Slot 7: unsupported file type
        self._unsupported_widget = UnsupportedFileWidget()
        self._preview_stack.addWidget(self._unsupported_widget)    # index 7

        # Slot 8: unreadable file (permission / access errors)
        self._unreadable_widget = UnreadableFileWidget()
        self._preview_stack.addWidget(self._unreadable_widget)     # index 8

        self._preview_stack.setCurrentIndex(_PREVIEW_EMPTY)

        # Metadata panel placeholder (populated in Stage 5)
        self._metadata_panel = QWidget()
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

        # --- Connections ---
        self._empty_state.select_folder_requested.connect(self.select_root_folder)
        self._tree_view.file_selected.connect(self._on_file_selected)

        # Preview widget signals
        self._symlink_widget.navigate_to_path.connect(self._navigate_to_path)
        self._folder_widget.navigate_to_path.connect(self._navigate_to_path)
        self._image_widget.load_failed.connect(self._on_preview_load_failed)
        self._text_widget.load_failed.connect(self._on_preview_load_failed)
        self._pdf_widget.load_failed.connect(self._on_preview_load_failed)

    # ------------------------------------------------------------------
    # Hashing engine signal connections
    # ------------------------------------------------------------------

    def _connect_engine_signals(self) -> None:
        self._hashing_engine.hashing_started.connect(self._on_hashing_started)
        self._hashing_engine.progress_updated.connect(self._on_progress_updated)
        self._hashing_engine.hashing_complete.connect(self._on_hashing_complete)
        self._hashing_engine.hashing_cancelled.connect(self._on_hashing_cancelled)

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
        self._preview_stack.setCurrentIndex(_PREVIEW_EMPTY)

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

        # Clear per-root session state in preview widgets
        self._image_widget.clear_session_state()
        self._pdf_widget.clear_session_state()

        # Build new model and wire it to the hashing engine
        self._tree_model = FileTreeModel(self._resolver)
        self._hashing_engine.file_hashed.connect(self._tree_model.on_file_hashed)
        self._tree_model.set_root(path)
        self._tree_view.setModel(self._tree_model)

        # Persist new root; clear expansion if changing root
        self._session.set_root(str(path))
        if not restore_expansion:
            self._session.clear_expansion_state()

        self.setWindowTitle(f"Echofinder — {path}")

        # Restore or reset expansion state
        if restore_expansion:
            self._restore_expansion_state()

        # Connect signals after model is set
        self._tree_view.expanded.connect(self._save_expansion_state)
        self._tree_view.collapsed.connect(self._save_expansion_state)
        self._tree_view.selectionModel().currentChanged.connect(
            self._on_selection_changed
        )

        # Show empty state (no file selected yet)
        self._current_preview_path = None
        self._preview_stack.setCurrentIndex(_PREVIEW_EMPTY)

        # Start background hashing for the new root
        self._hashing_engine.start_hashing(path)

    # ------------------------------------------------------------------
    # Hashing engine slots (called on main thread via queued connection)
    # ------------------------------------------------------------------

    def _on_hashing_started(self, total: int) -> None:
        if total == 0:
            return
        # Replace any transient status message (e.g. cache-reset warning)
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
        # Progress widgets will be re-created when the new root's hashing starts
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
        # Expand top-down (shortest path first so parents load before children)
        for path_str in sorted(paths, key=lambda p: len(Path(p).parts)):
            idx = self._tree_model.index_for_path(Path(path_str))
            if idx.isValid():
                self._tree_view.expand(idx)

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------

    def _on_selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            self._release_current_preview()
            self._preview_stack.setCurrentIndex(_PREVIEW_EMPTY)
            self._current_preview_path = None
            if self._tree_model is not None:
                self._tree_model.set_active_file(None)
            return

        node = current.internalPointer()

        # Release resource for whatever was showing before
        self._release_current_preview()

        self._current_preview_path = node.path
        self._activate_preview(node)

        # Update duplicate tracking
        if self._tree_model is not None:
            if not node.is_dir and not node.is_symlink:
                self._tree_model.set_active_file(str(node.path))
            else:
                self._tree_model.set_active_file(None)

    def _activate_preview(self, node) -> None:
        """Route the selected node to the appropriate preview widget."""
        path = node.path
        file_type = node.file_type

        if node.is_symlink:
            root = self._tree_model.root_path() if self._tree_model else None
            self._symlink_widget.load(path, root)
            self._preview_stack.setCurrentIndex(_PREVIEW_SYMLINK)
            return

        if node.is_dir:
            self._folder_widget.load(path)
            self._preview_stack.setCurrentIndex(_PREVIEW_FOLDER)
            return

        if file_type == FileType.IMAGE:
            self._image_widget.load(path)
            self._preview_stack.setCurrentIndex(_PREVIEW_IMAGE)

        elif file_type in (FileType.TEXT, FileType.CODE):
            self._text_widget.load(path)
            self._preview_stack.setCurrentIndex(_PREVIEW_TEXT)

        elif file_type == FileType.PDF:
            self._pdf_widget.load(path)
            self._preview_stack.setCurrentIndex(_PREVIEW_PDF)

        elif file_type in (FileType.VIDEO, FileType.AUDIO):
            self._media_widget.load(path)
            self._preview_stack.setCurrentIndex(_PREVIEW_MEDIA)

        else:
            # UNKNOWN — no preview available
            self._unsupported_widget.load(path)
            self._preview_stack.setCurrentIndex(_PREVIEW_UNSUPPORTED)

    def _release_current_preview(self) -> None:
        """Release resources from the widget currently shown in the stack."""
        current_idx = self._preview_stack.currentIndex()
        if current_idx == _PREVIEW_IMAGE:
            self._image_widget.release()
        elif current_idx == _PREVIEW_PDF:
            self._pdf_widget.release()
        elif current_idx == _PREVIEW_MEDIA:
            self._media_widget.stop_playback()

    def _on_preview_load_failed(self, error_type: str) -> None:
        """Called when a preview widget cannot read its file."""
        filename = (
            self._current_preview_path.name
            if self._current_preview_path is not None
            else "file"
        )
        self._unreadable_widget.show_error(error_type, filename)
        self._preview_stack.setCurrentIndex(_PREVIEW_UNREADABLE)

    def _on_file_selected(self, path: Path) -> None:
        # Enter key on a file — preview pane already updated via currentChanged
        pass

    # ------------------------------------------------------------------
    # Navigation from preview widgets (symlink jump, folder item click)
    # ------------------------------------------------------------------

    def _navigate_to_path(self, path: Path) -> None:
        """Navigate the tree to *path*, expanding the parent if needed."""
        if self._tree_model is None:
            return
        idx = self._tree_model.index_for_path(path)
        if not idx.isValid():
            return
        # Ensure the parent is expanded so the item is visible
        parent_idx = idx.parent()
        if parent_idx.isValid() and not self._tree_view.isExpanded(parent_idx):
            self._tree_view.expand(parent_idx)
        self._tree_view.setCurrentIndex(idx)
        self._tree_view.scrollTo(idx)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        # Stop media playback before closing (avoids VLC cleanup warnings)
        self._media_widget.stop_playback()
        # Cancel background hashing before the window closes to avoid Qt warnings
        if self._hashing_engine.isRunning():
            self._hashing_engine.cancel()
            self._hashing_engine.wait(5000)
        self._hash_cache.close()
        super().closeEvent(event)
