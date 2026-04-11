from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMenu, QTreeView

from echofinder.models.file_node import FileNode
from echofinder.ui.tree_model import FileTreeModel


class FileTreeView(QTreeView):
    """QTreeView with Echofinder keyboard navigation and context menu scaffold."""

    file_selected = pyqtSignal(Path)    # emitted when a file node is activated
    folder_selected = pyqtSignal(Path)  # emitted when a folder node is activated

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    # ------------------------------------------------------------------
    # Keyboard navigation (US-015)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        index = self.currentIndex()

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self._handle_left(index)
            event.accept()
            return

        if key == Qt.Key.Key_Right:
            self._handle_right(index)
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handle_enter(index)
            event.accept()
            return

        if key == Qt.Key.Key_F2:
            # Stage 6 stub: rename
            event.accept()
            return

        if key in (Qt.Key.Key_M, Qt.Key.Key_F6):
            # Stage 6 stub: move
            event.accept()
            return

        if key == Qt.Key.Key_Delete:
            # Stage 6 stub: delete
            event.accept()
            return

        super().keyPressEvent(event)

    def _handle_left(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        if node.is_dir and self.isExpanded(index):
            self.collapse(index)
        else:
            parent = index.parent()
            if parent.isValid():
                self.setCurrentIndex(parent)

    def _handle_right(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()

        if node.is_symlink:
            self._beep()
            return

        if node.is_dir:
            if not self.isExpanded(index):
                self.expand(index)
            else:
                model = self.model()
                first_child = model.index(0, 0, index)
                if first_child.isValid():
                    self.setCurrentIndex(first_child)
                else:
                    # Empty directory
                    self._beep()
        else:
            # Plain file — non-drillable
            self._beep()

    def _handle_enter(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        if node.is_dir:
            if self.isExpanded(index):
                self.collapse(index)
            else:
                self.expand(index)
        else:
            self.file_selected.emit(node.path)

    @staticmethod
    def _beep() -> None:
        try:
            QApplication.beep()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Context menu scaffold (Stage 6)
    # ------------------------------------------------------------------

    def _show_context_menu(self, position) -> None:
        index = self.indexAt(position)
        if not index.isValid():
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        rename_action.setEnabled(False)
        move_action = menu.addAction("Move")
        move_action.setEnabled(False)
        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(False)

        menu.exec(self.viewport().mapToGlobal(position))
