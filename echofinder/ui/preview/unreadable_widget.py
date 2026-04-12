"""Unreadable file widget (US-012, US-051).

Shown when a file cannot be read due to permissions or because it has
become inaccessible.  Raw system error messages are never surfaced to the
user — only plain-language descriptions.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class UnreadableWidget(QWidget):
    """Displays a contextually appropriate message for unreadable files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        self._heading = QLabel()
        self._heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_font = self._heading.font()
        heading_font.setPointSize(14)
        heading_font.setBold(True)
        self._heading.setFont(heading_font)
        self._heading.setStyleSheet("color: #c0392b;")

        self._detail = QLabel()
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: gray;")

        layout.addWidget(self._heading)
        layout.addWidget(self._detail)

    def show_for(self, path: Path, *, is_permission_error: bool) -> None:
        """Update the message for *path*; distinguish permission vs. inaccessible."""
        if is_permission_error:
            self._heading.setText("Permission denied")
            self._detail.setText(
                f"You don\u2019t have permission to read this file.\n\n{path.name}"
            )
        else:
            self._heading.setText("File not readable")
            self._detail.setText(
                f"This file could not be read. It may have been moved or deleted.\n\n"
                f"{path.name}"
            )
