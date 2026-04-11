from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt

from echofinder.models.file_node import FileNode, FileType
from echofinder.models.scanner import scan_directory
from echofinder.services.file_type import FileTypeResolver
from echofinder.ui.icons import icon_for_type


class FileTreeModel(QAbstractItemModel):
    def __init__(self, resolver: FileTypeResolver, parent=None) -> None:
        super().__init__(parent)
        self._resolver = resolver
        self._root: FileNode | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_root(self, path: Path) -> None:
        self.beginResetModel()
        self._root = FileNode(path=path, file_type=FileType.FOLDER)
        self.endResetModel()
        # Eagerly load the root's direct children — they are always visible
        if self.canFetchMore(QModelIndex()):
            self.fetchMore(QModelIndex())

    def root_path(self) -> Path | None:
        return self._root.path if self._root else None

    def index_for_path(self, path: Path) -> QModelIndex:
        """Return the model index for *path*, loading ancestor nodes as needed."""
        if self._root is None:
            return QModelIndex()
        try:
            rel_parts = path.relative_to(self._root.path).parts
        except ValueError:
            return QModelIndex()

        current_index = QModelIndex()
        current_node = self._root

        for part in rel_parts:
            if not current_node.children_loaded:
                self.fetchMore(current_index)
            if not current_node.children:
                return QModelIndex()
            found = False
            for child in current_node.children:
                if child.name == part:
                    current_index = self.createIndex(child.row, 0, child)
                    current_node = child
                    found = True
                    break
            if not found:
                return QModelIndex()

        return current_index

    # ------------------------------------------------------------------
    # QAbstractItemModel interface
    # ------------------------------------------------------------------

    def index(self, row: int, col: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, col, parent):
            return QModelIndex()
        parent_node = self._root if not parent.isValid() else parent.internalPointer()
        if parent_node is None or parent_node.children is None:
            return QModelIndex()
        if row < len(parent_node.children):
            return self.createIndex(row, col, parent_node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node: FileNode = index.internalPointer()
        parent_node = node.parent
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        return self.createIndex(parent_node.row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        node = self._root if not parent.isValid() else parent.internalPointer()
        if node is None or node.children is None:
            return 0
        return len(node.children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return self._root is not None
        node: FileNode = parent.internalPointer()
        if node.is_symlink or not node.is_dir:
            return False
        if node.children is not None:
            return len(node.children) > 0
        return True  # Unloaded directory — assume it has children

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = self._root if not parent.isValid() else (
            parent.internalPointer() if parent.isValid() else None
        )
        if node is None:
            return False
        if node.is_symlink or not node.is_dir:
            return False
        return not node.children_loaded

    def fetchMore(self, parent: QModelIndex) -> None:
        node = self._root if not parent.isValid() else parent.internalPointer()
        if node is None or node.children_loaded:
            return

        children = self._scan_children(node)
        if children:
            self.beginInsertRows(parent, 0, len(children) - 1)
            node.children = children
            self.endInsertRows()
        else:
            node.children = []
            # Notify view to re-check hasChildren (removes stale expand arrow)
            if parent.isValid():
                self.dataChanged.emit(parent, parent, [])

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node: FileNode = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name
        if role == Qt.ItemDataRole.DecorationRole:
            return icon_for_type(node.file_type)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_children(self, node: FileNode) -> list[FileNode]:
        root_path = self._root.path if self._root else None
        return scan_directory(node, root_path, self._resolver)
