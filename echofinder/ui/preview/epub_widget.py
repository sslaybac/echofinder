"""EPUB preview widget (Stage 11).

Uses ebooklib for parsing and QWebEngineView for rendering.
A QWebEngineUrlSchemeHandler serves embedded EPUB assets (images, CSS, fonts)
from the in-memory book via the custom ``echofinder-epub://`` scheme.

Both ebooklib and PyQt6-WebEngine are optional at import time; if either is
absent ``_WEBENGINE_AVAILABLE`` is False and ``EpubPreviewWidget`` is a no-op
stub so the application never crashes on import.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QUrl, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile,
        QWebEngineUrlRequestJob,
        QWebEngineUrlSchemeHandler,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    import ebooklib  # noqa: F401 — presence check
    from ebooklib import epub as _epub

    _WEBENGINE_AVAILABLE = True
except ImportError:
    _WEBENGINE_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────────────
# Scheme handler (only defined when WebEngine is available)
# ──────────────────────────────────────────────────────────────────────────────

if _WEBENGINE_AVAILABLE:
    class _EpubSchemeHandler(QWebEngineUrlSchemeHandler):
        """Serves in-memory EPUB assets for ``echofinder-epub://book/<href>``."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._book = None

        def set_book(self, book) -> None:
            self._book = book

        def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
            if self._book is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return

            path = job.requestUrl().path().lstrip("/")
            item = self._book.get_item_with_href(path)
            if item is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return

            mime = (item.media_type or "application/octet-stream").encode()
            buf = QBuffer(parent=job)
            buf.setData(QByteArray(item.content))
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(mime, buf)

    # Module-level singleton — created once, installed on the default profile.
    _handler: _EpubSchemeHandler | None = None

    def _get_handler() -> _EpubSchemeHandler:
        global _handler
        if _handler is None:
            _handler = _EpubSchemeHandler()
            QWebEngineProfile.defaultProfile().installUrlSchemeHandler(
                b"echofinder-epub", _handler
            )
        return _handler


# ──────────────────────────────────────────────────────────────────────────────
# Preview widget — full implementation
# ──────────────────────────────────────────────────────────────────────────────

if _WEBENGINE_AVAILABLE:

    _ZOOM_MIN = 0.5
    _ZOOM_MAX = 3.0
    _ZOOM_STEP = 1.1
    _ZOOM_DEFAULT = 1.0

    class EpubPreviewWidget(QWidget):
        """EPUB preview with chapter navigation, zoom, and per-session state."""

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)

            self._handler = _get_handler()
            self._doc = None           # ebooklib EpubBook | None
            self._chapters: list = []  # EpubHtml items from spine
            self._current_chapter = 0
            self._path_str: str | None = None
            self._restore_scroll = False

            # Session state — keyed by path_str
            self._zoom_state: dict[str, float] = {}
            self._chapter_state: dict[str, int] = {}
            # Keyed by (path_str, chapter_idx)
            self._scroll_state: dict[tuple[str, int], float] = {}

            # Web view
            self._view = QWebEngineView()
            self._view.installEventFilter(self)
            self._view.page().loadFinished.connect(self._on_load_finished)

            # Navigation bar
            self._prev_btn = QPushButton("◀ Previous")
            self._next_btn = QPushButton("Next ▶")
            self._chapter_label = QLabel()
            self._chapter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            nav = QHBoxLayout()
            nav.addWidget(self._prev_btn)
            nav.addWidget(self._chapter_label, 1)
            nav.addWidget(self._next_btn)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(self._view, 1)
            layout.addLayout(nav)

            self._prev_btn.clicked.connect(self._go_prev)
            self._next_btn.clicked.connect(self._go_next)

        # ------------------------------------------------------------------
        # Public API
        # ------------------------------------------------------------------

        def load(self, path: Path) -> None:
            """Open *path* and display the last-viewed (or first) chapter."""
            if self._doc is not None:
                self._save_scroll()

            path_str = str(path)
            try:
                book = _epub.read_epub(str(path))
            except Exception:
                self._doc = None
                self._handler.set_book(None)
                self._view.setUrl(QUrl("about:blank"))
                return

            self._doc = book
            self._path_str = path_str
            self._handler.set_book(book)

            # Build chapter list: spine items that are HTML documents
            self._chapters = []
            for spine_id, _ in book.spine:
                item = book.get_item_with_id(spine_id)
                if item is not None and isinstance(item, _epub.EpubHtml):
                    self._chapters.append(item)

            # Restore session state
            chapter_idx = self._chapter_state.get(path_str, 0)
            chapter_idx = max(0, min(chapter_idx, len(self._chapters) - 1)) if self._chapters else 0

            zoom = self._zoom_state.get(path_str, _ZOOM_DEFAULT)
            self._view.setZoomFactor(zoom)

            self._navigate_to_chapter(chapter_idx, restore_scroll=True)

        def release(self) -> None:
            """Save scroll state, navigate to blank, and drop the book reference."""
            if self._doc is not None:
                self._save_scroll()
            self._view.setUrl(QUrl("about:blank"))
            self._doc = None
            self._handler.set_book(None)
            self._chapters = []

        # ------------------------------------------------------------------
        # Chapter navigation
        # ------------------------------------------------------------------

        def _navigate_to_chapter(self, idx: int, restore_scroll: bool = False) -> None:
            if not self._chapters:
                self._update_nav()
                return

            self._current_chapter = idx
            if self._path_str is not None:
                self._chapter_state[self._path_str] = idx

            chapter = self._chapters[idx]
            html = chapter.get_content()

            # Base URL mirrors the chapter's directory so relative hrefs resolve
            file_name = chapter.file_name or ""
            base_dir = (file_name.rsplit("/", 1)[0] + "/") if "/" in file_name else ""
            base_url = QUrl(f"echofinder-epub://book/{base_dir}")

            self._restore_scroll = restore_scroll
            self._view.setContent(QByteArray(html), "text/html", base_url)
            self._update_nav()

        def _go_prev(self) -> None:
            if self._current_chapter > 0:
                self._save_scroll()
                self._navigate_to_chapter(self._current_chapter - 1, restore_scroll=True)

        def _go_next(self) -> None:
            if self._current_chapter < len(self._chapters) - 1:
                self._save_scroll()
                self._navigate_to_chapter(self._current_chapter + 1, restore_scroll=True)

        def _update_nav(self) -> None:
            total = len(self._chapters)
            self._prev_btn.setEnabled(self._current_chapter > 0)
            self._next_btn.setEnabled(self._current_chapter < total - 1)
            if total:
                self._chapter_label.setText(
                    f"Chapter {self._current_chapter + 1} of {total}"
                )
            else:
                self._chapter_label.setText("")

        # ------------------------------------------------------------------
        # Scroll state
        # ------------------------------------------------------------------

        def _save_scroll(self) -> None:
            if self._path_str is None:
                return
            key = (self._path_str, self._current_chapter)

            def _store(y) -> None:
                if y is not None:
                    self._scroll_state[key] = float(y)

            self._view.page().runJavaScript("window.scrollY", _store)

        def _on_load_finished(self, ok: bool) -> None:
            if not ok or not self._restore_scroll:
                return
            self._restore_scroll = False
            if self._path_str is None:
                return
            key = (self._path_str, self._current_chapter)
            saved_y = self._scroll_state.get(key, 0.0)
            if saved_y:
                self._view.page().runJavaScript(f"window.scrollTo(0, {saved_y})")

        # ------------------------------------------------------------------
        # Zoom
        # ------------------------------------------------------------------

        def _set_zoom(self, factor: float) -> None:
            factor = max(_ZOOM_MIN, min(_ZOOM_MAX, factor))
            self._view.setZoomFactor(factor)
            if self._path_str is not None:
                self._zoom_state[self._path_str] = factor

        def _zoom_in(self) -> None:
            self._set_zoom(self._view.zoomFactor() * _ZOOM_STEP)

        def _zoom_out(self) -> None:
            self._set_zoom(self._view.zoomFactor() / _ZOOM_STEP)

        def _zoom_reset(self) -> None:
            self._set_zoom(_ZOOM_DEFAULT)

        # ------------------------------------------------------------------
        # Event handling for keyboard and Ctrl+Scroll zoom
        # ------------------------------------------------------------------

        def keyPressEvent(self, event: QKeyEvent) -> None:
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._zoom_in()
            elif key == Qt.Key.Key_Minus:
                self._zoom_out()
            elif key == Qt.Key.Key_0 and mods & Qt.KeyboardModifier.ControlModifier:
                self._zoom_reset()
            else:
                super().keyPressEvent(event)

        def eventFilter(self, obj, event) -> bool:
            if obj is self._view and event.type() == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    if event.angleDelta().y() > 0:
                        self._zoom_in()
                    else:
                        self._zoom_out()
                    return True
            return super().eventFilter(obj, event)

else:
    # ──────────────────────────────────────────────────────────────────────
    # Stub — used when PyQt6-WebEngine or ebooklib is absent
    # ──────────────────────────────────────────────────────────────────────

    class EpubPreviewWidget(QWidget):  # type: ignore[no-redef]
        """No-op stub used when PyQt6-WebEngine is not installed."""

        def load(self, path: Path) -> None:
            pass

        def release(self) -> None:
            pass
