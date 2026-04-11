from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QWidget,
)

from echofinder.models.session import SessionState
from echofinder.services.file_type import FileTypeResolver
from echofinder.ui.empty_state import EmptyStateWidget
from echofinder.ui.file_tree_view import FileTreeView
from echofinder.ui.tree_model import FileTreeModel

# Index of the empty state widget within the preview QStackedWidget
_PREVIEW_EMPTY = 0
# Index of the selection placeholder
_PREVIEW_SELECTION = 1


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Echofinder")
        self.resize(1100, 700)

        self._session = SessionState()
        self._resolver = FileTypeResolver()
        self._tree_model: FileTreeModel | None = None

        self._build_ui()
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

        # Slot 0: empty state
        self._empty_state = EmptyStateWidget()
        self._preview_stack.addWidget(self._empty_state)

        # Slot 1: selection placeholder (replaces empty state once anything is selected)
        self._selection_label = QLabel()
        self._selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._selection_label.setWordWrap(True)
        self._preview_stack.addWidget(self._selection_label)

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

        # --- Status bar (empty; populated in Stages 2 and 6) ---
        self.setStatusBar(QStatusBar())

        # --- Connections ---
        self._empty_state.select_folder_requested.connect(self.select_root_folder)
        self._tree_view.file_selected.connect(self._on_file_selected)

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
        # Disconnect old expansion signals if a model already exists
        if self._tree_model is not None:
            try:
                self._tree_view.expanded.disconnect(self._save_expansion_state)
                self._tree_view.collapsed.disconnect(self._save_expansion_state)
                self._tree_view.selectionModel().currentChanged.disconnect(
                    self._on_selection_changed
                )
            except RuntimeError:
                pass

        # Build new model
        self._tree_model = FileTreeModel(self._resolver)
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
        self._preview_stack.setCurrentIndex(_PREVIEW_EMPTY)

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
            self._preview_stack.setCurrentIndex(_PREVIEW_EMPTY)
            return
        node = current.internalPointer()
        self._selection_label.setText(
            f"<b>{node.name}</b><br><br>"
            f"<span style='color: gray;'>{node.path}</span>"
        )
        self._preview_stack.setCurrentIndex(_PREVIEW_SELECTION)

    def _on_file_selected(self, path: Path) -> None:
        # Enter key on a file — preview pane already updated via currentChanged
        pass
