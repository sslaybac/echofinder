"""Symlink preview widget (US-052, US-053, US-054).

Displays the symlink name and target path.  When the resolved target falls
within the current root directory, a clickable jump link is shown.  When the
target is outside the root, a plain informational message is shown instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SymlinkWidget(QWidget):
    """Preview widget for symbolic links."""

    # Emitted when the user clicks the jump link; main_window navigates the tree.
    navigate_to_path = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._name_label.font()
        font.setPointSize(14)
        font.setBold(True)
        self._name_label.setFont(font)

        self._target_label = QLabel()
        self._target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._target_label.setWordWrap(True)

        self._info_label = QLabel()
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color: gray;")

        self._jump_btn = QPushButton()
        self._jump_btn.setFixedWidth(240)
        self._jump_btn.hide()

        layout.addWidget(self._name_label)
        layout.addWidget(self._target_label)
        layout.addWidget(self._info_label)
        layout.addWidget(self._jump_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._target_path: Path | None = None
        self._jump_btn.clicked.connect(self._on_jump_clicked)

    def load(self, path: Path, root: Path | None) -> None:
        """Display symlink information for *path*."""
        self._name_label.setText(path.name)
        self._target_path = None
        self._jump_btn.hide()

        try:
            raw_target = os.readlink(str(path))
        except OSError:
            self._target_label.setText("(could not read link target)")
            self._info_label.setText("")
            return

        target = Path(raw_target)
        if not target.is_absolute():
            target = (path.parent / target).resolve()
        else:
            target = target.resolve()

        self._target_label.setText(f"Target: {target}")

        # Check whether the target is within the current root
        if root is not None:
            try:
                target.relative_to(root.resolve())
                # Target is inside the root — show jump link
                self._info_label.setText("")
                self._target_path = target
                self._jump_btn.setText(f"Jump to target in tree")
                self._jump_btn.show()
                return
            except ValueError:
                pass

        # Target is outside the root (or no root set)
        self._info_label.setText(
            "This symlink points to a location outside the current root directory."
        )

    def _on_jump_clicked(self) -> None:
        if self._target_path is not None:
            self.navigate_to_path.emit(self._target_path)
