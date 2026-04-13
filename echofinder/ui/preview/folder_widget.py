"""Folder contents preview widget (US-055, US-056)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from echofinder.models.file_node import FileNode


class FolderContentsWidget(QWidget):
    """Flat listing of immediate children of the selected folder.

    Each item is a clickable button.  Clicking emits *navigate_to_path* with
    the child's Path so the main window can update the tree selection.
    """

    navigate_to_path = pyqtSignal(object)  # emits a Path

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the title, item count label, and scrollable child listing.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 8)
        outer.setSpacing(6)

        self._title = QLabel()
        title_font = self._title.font()
        title_font.setPointSize(13)
        title_font.setBold(True)
        self._title.setFont(title_font)
        outer.addWidget(self._title)

        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: gray;")
        outer.addWidget(self._count_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll)

        # Container widget inside the scroll area
        self._container = QWidget()
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(1)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._container)

    def load(self, folder_path: Path, children: list[FileNode]) -> None:
        """Populate the listing for *folder_path* with *children*."""
        self._title.setText(folder_path.name)

        # Clear previous items
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not children:
            self._count_label.setText("Empty folder")
            empty = QLabel("This folder is empty.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: gray; padding: 24px;")
            self._list_layout.addWidget(empty)
            return

        count = len(children)
        self._count_label.setText(
            f"{count} item{'s' if count != 1 else ''}"
        )

        for node in children:
            btn = _ChildButton(node.name, node.path)
            btn.navigate_to_path.connect(self.navigate_to_path)
            self._list_layout.addWidget(btn)


class _ChildButton(QPushButton):
    """A single clickable row in the folder listing."""

    navigate_to_path = pyqtSignal(object)  # emits a Path

    def __init__(self, name: str, path: Path) -> None:
        """Create a flat button row for *name* that emits *navigate_to_path* on click.

        Args:
            name: Display label for the button (typically the file or folder name).
            path: The ``Path`` emitted when the button is clicked.
        """
        super().__init__(name)
        self._path = path
        self.setFlat(True)
        self.setStyleSheet(
            "QPushButton { text-align: left; padding: 5px 8px; border: none; }"
            "QPushButton:hover {"
            "  background-color: palette(highlight);"
            "  color: palette(highlighted-text);"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._emit)

    def _emit(self) -> None:
        """Emit ``navigate_to_path`` with this button's ``Path``."""
        self.navigate_to_path.emit(self._path)
