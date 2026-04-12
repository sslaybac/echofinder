"""Text and code preview widget (US-045 – US-049).

Design notes:
- Receives pre-decoded text; no filesystem I/O here.
- Pygments is used for syntax highlighting; language is detected from the filename
  first, then by guessing from the content.  Falls back to plain text rendering.
- Text is always selectable and copyable (US-049).
- Monospaced font is enforced for both plain text and highlighted code (US-046).
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

try:
    from pygments import highlight as _pygments_highlight
    from pygments.formatters import HtmlFormatter as _HtmlFormatter
    from pygments.lexers import (
        TextLexer as _TextLexer,
        get_lexer_for_filename as _get_lexer_for_filename,
        guess_lexer as _guess_lexer,
    )
    from pygments.util import ClassNotFound as _ClassNotFound

    _PYGMENTS = True
except ImportError:
    _PYGMENTS = False

_MONO_CSS = "font-family: 'Courier New', Courier, monospace; font-size: 12px;"


class TextPreviewWidget(QWidget):
    """Displays text and code with optional Pygments syntax highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._edit = QTextEdit()
        self._edit.setReadOnly(True)
        self._edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        mono = QFont("Courier New", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._edit.setFont(mono)
        layout.addWidget(self._edit)

    def load(self, path: Path, text: str, encoding: str) -> None:  # noqa: ARG002
        """Display *text*; *encoding* is stored for the metadata panel (Stage 5)."""
        if _PYGMENTS:
            self._render_highlighted(path, text)
        else:
            self._edit.setPlainText(text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _render_highlighted(self, path: Path, text: str) -> None:
        lexer = _detect_lexer(path, text)
        if isinstance(lexer, _TextLexer):
            # Plain text: no highlighting, just monospaced rendering
            self._edit.setPlainText(text)
            return

        formatter = _HtmlFormatter(style="friendly", full=False, linenos=False)
        css = formatter.get_style_defs(".highlight")
        highlighted = _pygments_highlight(text, lexer, formatter)

        html = (
            "<!DOCTYPE html><html><head><style>\n"
            f"body {{ margin: 8px; {_MONO_CSS} }}\n"
            f".highlight pre {{ {_MONO_CSS} white-space: pre; margin: 0; }}\n"
            f"{css}\n"
            "</style></head><body>\n"
            f"{highlighted}\n"
            "</body></html>"
        )
        self._edit.setHtml(html)


def _detect_lexer(path: Path, text: str):
    """Return the best Pygments lexer for *path* / *text*."""
    # 1. Try filename (extension-based)
    try:
        return _get_lexer_for_filename(path.name)
    except _ClassNotFound:
        pass

    # 2. Try content analysis
    try:
        return _guess_lexer(text)
    except _ClassNotFound:
        pass

    # 3. Plain text
    return _TextLexer()
