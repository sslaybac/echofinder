"""PDF preview widget (US-042, US-043, US-044).

Design notes:
- Uses PyMuPDF (fitz) to render pages at display resolution (fit-to-width × zoom).
- Zoom is relative to fit-to-width; retained per file for the duration of the session.
- Scroll position is retained per file for the duration of the session.
- Pages are lazy-rendered: only pages within the visible area (plus a buffer) are
  rendered; unrendered pages are shown as sized white placeholders.
- release() closes the fitz document and clears all rendered images, ensuring only
  one file's preview data is in memory at a time.
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_ZOOM_STEP = 1.25
_ZOOM_MIN = 0.1
_ZOOM_MAX = 8.0
_PAGE_GAP = 12          # vertical gap between pages in pixels
_RENDER_MARGIN = 1.5    # render pages within N viewport-heights of the visible area


class _PageLabel(QLabel):
    """Sized placeholder for one rendered PDF page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an initially unrendered page placeholder label.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: white; border: 1px solid #d0d0d0;")
        self._rendered = False

    @property
    def rendered(self) -> bool:
        """``True`` if a pixmap has been set on this label.

        Returns:
            Whether this page has been rendered.
        """
        return self._rendered

    def set_rendered(self, pixmap: QPixmap) -> None:
        """Set *pixmap* on the label and mark the page as rendered.

        Args:
            pixmap: The rendered page pixmap.
        """
        self.setPixmap(pixmap)
        self._rendered = True

    def clear_render(self) -> None:
        """Clear the pixmap and mark the page as unrendered (used during zoom changes)."""
        self.clear()
        self._rendered = False


class PDFPreviewWidget(QWidget):
    """Renders PDF pages using PyMuPDF with zoom, scroll, and per-file session state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the scroll area, page container, and deferred-render timer.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        layout.addWidget(self._scroll)

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(8, 8, 8, 8)
        self._container_layout.setSpacing(_PAGE_GAP)
        self._container_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        self._scroll.setWidget(self._container)

        # Per-file session state (in-memory only; resets on application relaunch)
        self._zoom_state: dict[str, float] = {}
        self._scroll_state: dict[str, int] = {}

        self._doc: fitz.Document | None = None
        self._current_path: str | None = None
        self._page_labels: list[_PageLabel] = []

        # Deferred render: batch rapid scroll/resize events into one render pass
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._render_visible_pages)

        self._scroll.viewport().installEventFilter(self)
        self._scroll.verticalScrollBar().valueChanged.connect(
            lambda _: self._render_timer.start()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Open *path* and display its pages; restore session zoom and scroll."""
        # Persist scroll position for the file we're leaving
        if self._current_path is not None:
            self._scroll_state[self._current_path] = (
                self._scroll.verticalScrollBar().value()
            )

        self._release_doc()

        path_key = str(path)
        self._current_path = path_key

        try:
            self._doc = fitz.open(str(path))
        except Exception:
            self._show_error("Unable to open this PDF.")
            return

        if self._doc.page_count == 0:
            self._show_error("This PDF has no pages.")
            return

        self._build_page_placeholders()

        # Restore scroll position after Qt has applied the layout
        saved_scroll = self._scroll_state.get(path_key, 0)
        if saved_scroll > 0:
            QTimer.singleShot(
                0, lambda v=saved_scroll: self._scroll.verticalScrollBar().setValue(v)
            )

        self._render_timer.start()

    def release(self) -> None:
        """Release the PDF document and all rendered images."""
        if self._current_path is not None:
            self._scroll_state[self._current_path] = (
                self._scroll.verticalScrollBar().value()
            )
        self._release_doc()
        self._current_path = None

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Rebuild page placeholders at the current zoom when the pane is resized."""
        super().resizeEvent(event)
        if self._doc is not None:
            zoom = self._zoom_state.get(self._current_path or "", 1.0)
            self._rebuild_placeholders_at_zoom(zoom)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle ``+``/``-`` zoom keys and ``Ctrl+0`` reset.

        Args:
            event: The key event to process.
        """
        key = event.key()
        mods = event.modifiers()
        ctrl = Qt.KeyboardModifier.ControlModifier

        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(_ZOOM_STEP)
            event.accept()
        elif key == Qt.Key.Key_Minus:
            self._zoom_by(1.0 / _ZOOM_STEP)
            event.accept()
        elif key == Qt.Key.Key_0 and (mods & ctrl):
            self._apply_zoom(1.0)
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Ctrl+Scroll → zoom; plain scroll → pan (passed through)."""
        if obj is self._scroll.viewport() and isinstance(event, QWheelEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._zoom_by(_ZOOM_STEP)
                elif delta < 0:
                    self._zoom_by(1.0 / _ZOOM_STEP)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _zoom_by(self, factor: float) -> None:
        """Multiply the current zoom by *factor*, clamped to [``_ZOOM_MIN``, ``_ZOOM_MAX``].

        Args:
            factor: Multiplicative zoom step (e.g. ``_ZOOM_STEP`` = 1.25 to zoom in).
        """
        key = self._current_path or ""
        current = self._zoom_state.get(key, 1.0)
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, current * factor))
        self._apply_zoom(new_zoom)

    def _apply_zoom(self, zoom: float) -> None:
        """Persist *zoom* for the current path and rebuild page placeholders.

        Args:
            zoom: New zoom factor relative to fit-to-width (1.0 = fit exactly).
        """
        if self._current_path is not None:
            self._zoom_state[self._current_path] = zoom
        if self._doc is not None:
            self._rebuild_placeholders_at_zoom(zoom)

    def _avail_width(self) -> int:
        """Usable page-rendering width (viewport minus layout margins)."""
        vp_w = self._scroll.viewport().width()
        m = self._container_layout.contentsMargins()
        return max(100, vp_w - m.left() - m.right())

    def _page_render_size(self, page_idx: int, zoom: float) -> tuple[int, int]:
        """Pixel dimensions for page *page_idx* at *zoom* (fit-to-width × zoom)."""
        rect = self._doc[page_idx].rect
        avail = self._avail_width()
        scale = (avail / rect.width) * zoom
        return max(1, int(rect.width * scale)), max(1, int(rect.height * scale))

    def _build_page_placeholders(self) -> None:
        """Clear the layout and create one sized placeholder per page."""
        self._clear_page_labels()
        zoom = self._zoom_state.get(self._current_path or "", 1.0)
        for i in range(self._doc.page_count):
            w, h = self._page_render_size(i, zoom)
            lbl = _PageLabel(self._container)
            lbl.setFixedSize(w, h)
            self._container_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._page_labels.append(lbl)

    def _rebuild_placeholders_at_zoom(self, zoom: float) -> None:
        """Resize existing placeholders and invalidate their rendered content."""
        if not self._page_labels or self._doc is None:
            return
        for i, lbl in enumerate(self._page_labels):
            w, h = self._page_render_size(i, zoom)
            lbl.setFixedSize(w, h)
            lbl.clear_render()
        self._render_timer.start()

    def _render_visible_pages(self) -> None:
        """Render any unrendered page that falls within the visible + buffer area."""
        if self._doc is None or not self._page_labels:
            return

        viewport = self._scroll.viewport()
        scroll_top = self._scroll.verticalScrollBar().value()
        scroll_bottom = scroll_top + viewport.height()
        buffer = viewport.height() * _RENDER_MARGIN

        zoom = self._zoom_state.get(self._current_path or "", 1.0)
        avail = self._avail_width()

        for i, lbl in enumerate(self._page_labels):
            # lbl.y() is the label's y-coordinate within _container, which is the
            # same coordinate space as the scroll bar value.
            lbl_top = lbl.y()
            lbl_bottom = lbl_top + lbl.height()
            in_range = (
                lbl_bottom >= scroll_top - buffer
                and lbl_top <= scroll_bottom + buffer
            )
            if in_range and not lbl.rendered:
                self._render_page_into(i, lbl, zoom, avail)

    def _render_page_into(
        self, idx: int, lbl: _PageLabel, zoom: float, avail_w: int
    ) -> None:
        """Render page *idx* at display resolution and set its pixmap on *lbl*."""
        page = self._doc[idx]
        rect = page.rect
        scale = (avail_w / rect.width) * zoom
        matrix = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False)

        image = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,
        )
        lbl.set_rendered(QPixmap.fromImage(image))

    def _clear_page_labels(self) -> None:
        """Remove all page labels from the layout and schedule them for deletion."""
        while self._container_layout.count() > 0:
            item = self._container_layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        self._page_labels.clear()

    def _release_doc(self) -> None:
        """Close the fitz document and clear all rendered pages."""
        self._clear_page_labels()
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    def _show_error(self, msg: str) -> None:
        """Display an error message in the content area."""
        self._clear_page_labels()
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: gray; padding: 20px;")
        self._container_layout.addWidget(lbl)
