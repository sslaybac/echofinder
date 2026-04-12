"""Unsupported file type widget (US-050).

Displayed when the file type resolver finds no matching preview widget.
Shows a clear, informative message; never shows a raw system error.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class UnsupportedFileWidget(QWidget):
    """Preview widget for unsupported file types."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        icon = QLabel("🚫")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = icon.font()
        font.setPointSize(32)
        icon.setFont(font)

        self._title = QLabel("Preview not available")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = self._title.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self._title.setFont(title_font)

        self._detail = QLabel()
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: gray;")

        layout.addWidget(icon)
        layout.addWidget(self._title)
        layout.addWidget(self._detail)

    def load(self, path: Path) -> None:
        """Display the unsupported-type message for *path*."""
        suffix = path.suffix or "(no extension)"
        self._detail.setText(
            f"Echofinder cannot preview files of this type ({suffix}).\n\n"
            "Open the file in an external application to view its contents."
        )
