"""Unsupported file type widget (US-050).

Shown for any file type that has no preview widget in the current stage.
Audio and video are deferred to Stages 9 and 10 respectively.
PDF is handled by PDFPreviewWidget (Stage 8) and no longer routes here.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from echofinder.models.file_node import FileType

# Human-readable descriptions per deferred/unsupported type
_TYPE_LABEL: dict[FileType, str] = {
    FileType.AUDIO: "Audio files",
    FileType.VIDEO: "Video files",
}


class UnsupportedWidget(QWidget):
    """Displays a clear message when a file type has no preview widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the heading and detail labels; content is set by ``show_for``.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        self._heading = QLabel("Preview not available")
        self._heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_font = self._heading.font()
        heading_font.setPointSize(14)
        heading_font.setBold(True)
        self._heading.setFont(heading_font)
        self._heading.setStyleSheet("color: gray;")

        self._detail = QLabel()
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: gray;")

        layout.addWidget(self._heading)
        layout.addWidget(self._detail)

    def show_for(self, path: Path, file_type: FileType) -> None:
        """Update the message for *path* of *file_type*."""
        type_label = _TYPE_LABEL.get(file_type, "This file type")
        self._detail.setText(
            f"{type_label} cannot be previewed in this version of Echofinder.\n\n"
            f"{path.name}"
        )
