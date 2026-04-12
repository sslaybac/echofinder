"""Folder contents preview widget (US-055, US-056).

Displays a flat listing of a selected folder's immediate children.  Each item
is a clickable button; clicking updates the tree selection and main pane —
identical in effect to clicking that item directly in the file tree.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class FolderContentsWidget(QWidget):
    """Preview widget for directories."""

    # Emitted when the user clicks a child item.
    navigate_to_path = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QLabel()
        self._header.setContentsMargins(8, 8, 8, 4)
        font = self._header.font()
        font.setBold(True)
        self._header.setFont(font)
        outer.addWidget(self._header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        self._container = QWidget()
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(2)
        self._list_layout.setContentsMargins(8, 4, 8, 8)
        scroll.setWidget(self._container)

    def load(self, path: Path) -> None:
        """List the immediate children of *path*."""
        # Clear previous items
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._header.setText(f"Contents of {path.name}/")

        try:
            entries = sorted(
                path.iterdir(),
                key=lambda e: (e.is_symlink() or not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            self._list_layout.addWidget(
                _info_label("Permission denied — cannot list folder contents.")
            )
            return
        except OSError:
            self._list_layout.addWidget(
                _info_label("Folder is not accessible.")
            )
            return

        if not entries:
            self._list_layout.addWidget(_info_label("This folder is empty."))
            return

        for entry in entries:
            btn = _ItemButton(entry)
            btn.clicked_path.connect(self.navigate_to_path)
            self._list_layout.addWidget(btn)


class _ItemButton(QPushButton):
    """A flat button representing a single directory entry."""

    clicked_path = pyqtSignal(Path)

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        label = ("📁 " if path.is_dir() and not path.is_symlink() else "") + path.name
        super().__init__(label, parent)
        self._path = path
        self.setFlat(True)
        self.setStyleSheet(
            "QPushButton { text-align: left; padding: 4px 8px; }"
            "QPushButton:hover { background-color: palette(highlight); color: palette(highlighted-text); }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._emit)

    def _emit(self) -> None:
        self.clicked_path.emit(self._path)


def _info_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: gray;")
    lbl.setContentsMargins(0, 8, 0, 0)
    return lbl
