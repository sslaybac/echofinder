"""Symlink preview widget (US-052, US-053, US-054)."""
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
    """Displays symlink name and target path.

    If the target is within the current root, shows a clickable jump link
    that emits *navigate_to_path*.  If outside the root, shows a plain
    informational message.
    """

    navigate_to_path = pyqtSignal(object)  # emits a Path

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the name, kind, target, and optional jump-button labels.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_font = self._name_label.font()
        name_font.setPointSize(14)
        name_font.setBold(True)
        self._name_label.setFont(name_font)

        self._kind_label = QLabel("Symbolic link")
        self._kind_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kind_font = self._kind_label.font()
        kind_font.setItalic(True)
        self._kind_label.setFont(kind_font)
        self._kind_label.setStyleSheet("color: gray;")

        self._target_label = QLabel()
        self._target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._target_label.setWordWrap(True)
        self._target_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._jump_btn = QPushButton()
        self._jump_btn.setFixedWidth(240)
        self._jump_btn.hide()

        self._outside_label = QLabel()
        self._outside_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._outside_label.setWordWrap(True)
        self._outside_label.setStyleSheet("color: gray;")
        self._outside_label.hide()

        layout.addWidget(self._name_label)
        layout.addWidget(self._kind_label)
        layout.addSpacing(12)
        layout.addWidget(self._target_label)
        layout.addSpacing(8)
        layout.addWidget(self._jump_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._outside_label)

        self._jump_target: Path | None = None
        self._jump_btn.clicked.connect(self._on_jump_clicked)

    def load(self, symlink_path: Path, root: Path | None) -> None:
        """Populate the widget for *symlink_path*."""
        self._name_label.setText(symlink_path.name)
        self._jump_target = None
        self._jump_btn.hide()
        self._outside_label.hide()

        try:
            raw = os.readlink(str(symlink_path))
            target = Path(raw)
            if not target.is_absolute():
                target = (symlink_path.parent / target).resolve()
            else:
                target = target.resolve()
        except OSError:
            self._target_label.setText("Target path could not be read.")
            return

        self._target_label.setText(f"Target:  {target}")

        if root is not None and _is_within(target, root.resolve()):
            self._jump_target = target
            self._jump_btn.setText(f"Jump to \u2192 {target.name}")
            self._jump_btn.show()
        else:
            self._outside_label.setText(
                "The target is outside the current root folder.\n"
                "Open it in a file manager to navigate there."
            )
            self._outside_label.show()

    def _on_jump_clicked(self) -> None:
        """Emit ``navigate_to_path`` with the resolved symlink target."""
        if self._jump_target is not None:
            self.navigate_to_path.emit(self._jump_target)


def _is_within(path: Path, root: Path) -> bool:
    """Return ``True`` if *path* is at or below *root* in the directory tree.

    Args:
        path: The path to test.
        root: The root directory to test against (should already be resolved).

    Returns:
        ``True`` if *path* is relative to *root*.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
