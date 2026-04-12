"""Unreadable file widget (US-012, US-051).

Displayed when a file cannot be read — due to permission denial or because it
has become inaccessible.  Permission-specific messaging is distinct from the
general inaccessibility message.  No raw system error is shown.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class UnreadableFileWidget(QWidget):
    """Preview widget for files that cannot be read."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        icon = QLabel("🔒")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = icon.font()
        font.setPointSize(32)
        icon.setFont(font)

        self._title = QLabel()
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

        self._icon_label = icon  # keep reference so we can swap the emoji

    def show_error(self, error_type: str, filename: str) -> None:
        """Display an appropriate error message.

        Parameters
        ----------
        error_type:
            ``"permission"`` for a PermissionError (US-012),
            ``"access"`` for a general OSError / file-became-inaccessible
            scenario (US-051).
        filename:
            The name of the file that could not be read (for display only).
        """
        if error_type == "permission":
            self._icon_label.setText("\U0001f512")
            self._title.setText("Access denied")
            self._detail.setText(
                f'You don\'t have permission to read \u201c{filename}\u201d.\n\n'
                "Check the file's permissions or ask the file owner for access."
            )
        else:
            self._icon_label.setText("\u26a0\ufe0f")
            self._title.setText("File not accessible")
            self._detail.setText(
                f'\u201c{filename}\u201d could not be read.\n\n'
                "The file may have been moved, deleted, or become temporarily "
                "unavailable."
            )
