"""Text and code preview widget (US-045 through US-049).

Displays plain text and code files in a monospaced font.  Code files receive
Pygments syntax highlighting.  Encoding is detected via the cascade:
  1. UTF-8 (handles pure ASCII too)
  2. charset-normalizer detection
  3. Latin-1 unconditional fallback (maps every byte value)

The detected encoding name is emitted via ``encoding_detected`` for the Stage 5
metadata panel.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import TextLexer, get_lexer_for_filename, guess_lexer
    from pygments.util import ClassNotFound

    _PYGMENTS_AVAILABLE = True
except ImportError:
    _PYGMENTS_AVAILABLE = False

try:
    from charset_normalizer import from_bytes as _cn_from_bytes

    _CHARSET_NORMALIZER_AVAILABLE = True
except ImportError:
    _CHARSET_NORMALIZER_AVAILABLE = False

# Preview is capped to avoid loading very large files into the text widget.
_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB


class TextPreviewWidget(QWidget):
    """Preview widget for text and code files."""

    load_failed = pyqtSignal(str)        # "permission" | "access"
    encoding_detected = pyqtSignal(str)  # encoding name, consumed by Stage 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)

        # Monospaced font (US-046)
        mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self._text_edit.setFont(mono)

        layout.addWidget(self._text_edit)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Load and display the text/code file at *path*."""
        try:
            raw = self._read_raw(path)
        except PermissionError:
            self.load_failed.emit("permission")
            return
        except OSError:
            self.load_failed.emit("access")
            return

        # Truncate oversized files before decoding
        truncated = len(raw) > _MAX_BYTES
        if truncated:
            raw = raw[:_MAX_BYTES]

        text, encoding = self._decode(raw)

        if truncated:
            text += f"\n\n[Preview truncated — file exceeds {_MAX_BYTES // (1024*1024)} MiB]"

        self.encoding_detected.emit(encoding)

        if _PYGMENTS_AVAILABLE:
            html, _lang = self._highlight(text, path.name)
            self._text_edit.setHtml(html)
        else:
            self._text_edit.setPlainText(text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_raw(path: Path) -> bytes:
        return path.read_bytes()

    @staticmethod
    def _decode(raw: bytes) -> tuple[str, str]:
        """Apply the encoding cascade and return (text, encoding_name)."""
        # 1. UTF-8 (also accepts pure ASCII)
        try:
            return raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            pass

        # 2. charset-normalizer detection
        if _CHARSET_NORMALIZER_AVAILABLE:
            result = _cn_from_bytes(raw).best()
            if result is not None and result.encoding:
                try:
                    return raw.decode(result.encoding), result.encoding
                except (UnicodeDecodeError, LookupError):
                    pass

        # 3. Latin-1 unconditional fallback — maps every possible byte value
        return raw.decode("latin-1"), "latin-1"

    @staticmethod
    def _highlight(text: str, filename: str) -> tuple[str, str]:
        """Return (full_html, language_name) with Pygments syntax highlighting."""
        try:
            lexer = get_lexer_for_filename(filename, stripall=True)
        except ClassNotFound:
            try:
                lexer = guess_lexer(text[:4096])
            except ClassNotFound:
                lexer = TextLexer()

        formatter = HtmlFormatter(
            style="friendly",
            noclasses=True,
            prestyles=(
                "font-family: 'Courier New', Courier, monospace;"
                " font-size: 11pt;"
                " white-space: pre-wrap;"
                " word-wrap: break-word;"
                " margin: 8px;"
            ),
        )
        body = highlight(text, lexer, formatter)
        full_html = (
            "<html><head><meta charset='utf-8'/>"
            "<style>body { margin: 0; padding: 0; }</style></head>"
            f"<body>{body}</body></html>"
        )
        return full_html, lexer.name
