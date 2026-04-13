from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class EmptyStateWidget(QWidget):
    """Shown in the preview pane before any root is selected (US-057, US-058)."""

    select_folder_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        """Build the welcome screen layout with a 'Select Root Folder' button."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        title = QLabel("Welcome to Echofinder")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)

        subtitle = QLabel("Find and manage duplicate files in any folder.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        instructions = QLabel(
            "To get started, choose a root folder.\n"
            "Echofinder will scan it and identify duplicate files."
        )
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instructions.setWordWrap(True)

        select_btn = QPushButton("Select Root Folder")
        select_btn.setFixedWidth(180)
        select_btn.clicked.connect(self.select_folder_requested)

        btn_row = QWidget()
        btn_layout = QVBoxLayout(btn_row)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addWidget(select_btn)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addWidget(instructions)
        layout.addSpacing(4)
        layout.addWidget(btn_row)
