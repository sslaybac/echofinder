"""Metadata panel — read-only strip below the preview pane (Stage 5).

Displays hash, MIME type, programming language, detected encoding, and
duplicate count for the currently selected file.  Hidden when a folder,
symlink, or nothing is selected.

Module boundaries: no direct SQLite access here; all data flows through
HashCache accessor methods.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from echofinder.models.hash_cache import HashCache


class MetadataPanel(QWidget):
    """Compact two-column form showing file metadata below the preview pane."""

    navigate_to_path = pyqtSignal(object)  # Path; forwarded to MainWindow

    def __init__(self, hash_cache: HashCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hash_cache = hash_cache
        self._current_path: str | None = None
        self._current_hash: str | None = None
        self._dup_paths: list[str] = []

        self._build_ui()
        self.hide()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form.setSpacing(3)

        # Hash row — selectable monospaced QLineEdit
        self._hash_edit = QLineEdit()
        self._hash_edit.setReadOnly(True)
        self._hash_edit.setFrame(False)
        self._hash_edit.setStyleSheet("background: transparent;")
        mono = QFont("Courier New", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._hash_edit.setFont(mono)
        self._form.addRow("Hash:", self._hash_edit)

        # Type row
        self._type_label = QLabel()
        self._form.addRow("Type:", self._type_label)

        # Language row — hidden when not applicable
        self._lang_key = QLabel("Language:")
        self._lang_key.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lang_label = QLabel()
        self._form.addRow(self._lang_key, self._lang_label)
        self._lang_key.hide()
        self._lang_label.hide()

        # Encoding row — hidden when not applicable
        self._enc_key = QLabel("Encoding:")
        self._enc_key.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._enc_label = QLabel()
        self._form.addRow(self._enc_key, self._enc_label)
        self._enc_key.hide()
        self._enc_label.hide()

        # Duplicates row — clickable when count > 0
        self._dup_button = QPushButton()
        self._dup_button.setFlat(True)
        self._dup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dup_button.setStyleSheet(
            "QPushButton { text-align: left; padding: 0px; border: none; }"
            "QPushButton:enabled { color: palette(link); }"
            "QPushButton:disabled { color: palette(text); }"
        )
        self._dup_button.clicked.connect(self._show_duplicate_menu)
        self._form.addRow("Duplicates:", self._dup_button)

        outer.addLayout(self._form)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def display_file(self, path: str) -> None:
        """Populate all fields for *path* and show the panel."""
        self._current_path = path
        self._current_hash = None
        self._dup_paths = []

        meta = self._hash_cache.get_file_metadata(path)

        if meta is None:
            # Not yet hashed — show placeholders
            self._hash_edit.setText("Hashing\u2026")
            self._type_label.setText("Hashing\u2026")
            self._set_language(None)
            self._set_duplicates_hashing()
        else:
            self._current_hash = meta["hash"]
            self._apply_metadata(meta)

        self.show()

    def on_file_hashed(
        self, path: str, hash_val: str, filetype: str, language: str
    ) -> None:
        """Refresh the panel if the newly hashed file is currently displayed."""
        if path != self._current_path:
            return
        self._current_hash = hash_val or None
        # Treat empty strings from the engine as absent values
        self._apply_metadata({
            "hash": hash_val or None,
            "filetype": filetype or None,
            "language": language or None,
        })

    def set_encoding(self, encoding_name: str) -> None:
        """Show the encoding row with *encoding_name*, or hide it if empty."""
        if encoding_name:
            self._enc_label.setText(encoding_name)
            self._enc_key.show()
            self._enc_label.show()
        else:
            self._enc_key.hide()
            self._enc_label.hide()

    def clear(self) -> None:
        """Hide the panel (nothing selected or root changed)."""
        self._current_path = None
        self._current_hash = None
        self._dup_paths = []
        self.hide()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_metadata(self, meta: dict) -> None:
        hash_val: str | None = meta.get("hash")
        filetype: str | None = meta.get("filetype")
        language: str | None = meta.get("language")

        self._hash_edit.setText(hash_val if hash_val else "Unknown")
        self._type_label.setText(filetype if filetype else "Unknown")
        self._set_language(language)

        if hash_val:
            dup_paths = self._hash_cache.get_duplicate_paths(
                hash_val, self._current_path or ""
            )
            self._dup_paths = dup_paths
            count = len(dup_paths)
            word = "duplicate" if count == 1 else "duplicates"
            self._dup_button.setText(f"{count} {word}")
            self._dup_button.setEnabled(count > 0)
        else:
            self._dup_paths = []
            self._dup_button.setText("Unknown")
            self._dup_button.setEnabled(False)

    def _set_language(self, language: str | None) -> None:
        if language:
            self._lang_label.setText(language)
            self._lang_key.show()
            self._lang_label.show()
        else:
            self._lang_key.hide()
            self._lang_label.hide()

    def _set_duplicates_hashing(self) -> None:
        self._dup_button.setText("Hashing\u2026")
        self._dup_button.setEnabled(False)

    def _show_duplicate_menu(self) -> None:
        if not self._dup_paths:
            return
        menu = QMenu(self)
        for path_str in self._dup_paths:
            action = menu.addAction(path_str)
            action.triggered.connect(
                lambda _checked, p=path_str: self.navigate_to_path.emit(Path(p))
            )
        menu.exec(
            self._dup_button.mapToGlobal(self._dup_button.rect().bottomLeft())
        )
