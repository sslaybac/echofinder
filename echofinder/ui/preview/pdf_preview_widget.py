"""PDF preview widget (US-042, US-043, US-044).

Renders PDFs via PyMuPDF (fitz) at pane-appropriate resolution.  Supports
multi-page scrolling and per-file zoom state retained for the session.  The
open document is released when a new file is loaded.

Threading model
---------------
Every ``fitz.open()`` call is performed on a background daemon thread so that
the main thread is never blocked — even the initial probe that determines page
count and dimensions.

Each render run is identified by a monotonically increasing ``_render_id``.
Background threads capture the render_id at launch and drop any result whose
id no longer matches the current value (i.e. the user navigated away or changed
zoom mid-render).

Two signals carry data from worker threads to the main thread:
  * ``_probe_done``  — (render_id, "ok",         n_pages, zoom, saved_scroll)
                     OR (render_id, "permission")
                     OR (render_id, "access")
  * ``_page_ready``  — (render_id, page_idx, samples_bytes, w, h, stride)

The worker thread that opens the file for a ``load()`` call also renders all
pages in the same pass, so the file is opened exactly once per load.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    import fitz  # PyMuPDF

    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False

_ZOOM_STEP = 1.414213562  # √2
_ZOOM_MIN = 0.25
_ZOOM_MAX = 8.0


@dataclass
class _PDFState:
    zoom: float = 1.0
    scroll_x: int = 0
    scroll_y: int = 0


class PdfPreviewWidget(QWidget):
    """Preview widget for PDF files."""

    load_failed = pyqtSignal(str)   # "permission" | "access"
    _probe_done = pyqtSignal(object)
    _page_ready = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._state_cache: dict[str, _PDFState] = {}
        self._current_path: Path | None = None
        self._zoom_level: float = 1.0
        self._first_page_width: float = 0.0   # cached after first probe
        self._n_pages: int = 0                 # cached after first probe
        self._render_id: int = 0
        self._page_labels: list[QLabel] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 2, 6, 2)
        tb_layout.setSpacing(4)

        self._zoom_out_btn = QPushButton("\u2212")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedWidth(40)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(52)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label = QLabel("")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tb_layout.addWidget(self._zoom_out_btn)
        tb_layout.addWidget(self._zoom_label)
        tb_layout.addWidget(self._zoom_in_btn)
        tb_layout.addWidget(self._fit_btn)
        tb_layout.addSpacing(12)
        tb_layout.addWidget(self._page_label)
        tb_layout.addStretch()

        layout.addWidget(toolbar)

        # Scroll area containing a vertical stack of page labels
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._pages_container = QWidget()
        self._pages_layout = QVBoxLayout(self._pages_container)
        self._pages_layout.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._pages_layout.setSpacing(8)
        self._pages_layout.setContentsMargins(8, 8, 8, 8)
        self._scroll_area.setWidget(self._pages_container)

        layout.addWidget(self._scroll_area)

        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._fit_btn.clicked.connect(self._fit_width)
        self._probe_done.connect(self._on_probe_done)
        self._page_ready.connect(self._on_page_ready)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Open and render the PDF at *path*.

        Returns immediately after showing a "Loading…" placeholder.  All
        file I/O and rendering happen on a daemon thread; results are
        delivered back to the main thread via Qt signals.
        """
        self._save_scroll_state()
        self._current_path = path

        if not _FITZ_AVAILABLE:
            self._show_message("PyMuPDF not available.")
            return

        self._render_id += 1
        render_id = self._render_id

        self._clear_pages()
        self._show_message("Loading\u2026")

        saved = self._state_cache.get(str(path))
        saved_zoom: float | None = saved.zoom if saved is not None else None
        saved_scroll: tuple[int, int] | None = (
            (saved.scroll_x, saved.scroll_y) if saved is not None else None
        )
        pane_w = max(self._scroll_area.viewport().width() - 20, 1)
        path_str = str(path)
        probe_sig = self._probe_done
        page_sig = self._page_ready

        def _worker() -> None:
            try:
                doc = fitz.open(path_str)
            except PermissionError:
                probe_sig.emit((render_id, "permission"))
                return
            except Exception:
                probe_sig.emit((render_id, "access"))
                return

            try:
                n_pages = len(doc)
                page_w = doc[0].rect.width if n_pages > 0 else 0.0
                if saved_zoom is not None:
                    zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, saved_zoom))
                elif page_w > 0:
                    zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, pane_w / page_w))
                else:
                    zoom = 1.0

                probe_sig.emit((render_id, "ok", n_pages, page_w, zoom, saved_scroll))

                for i in range(n_pages):
                    if render_id != self._render_id:
                        return  # cancelled by newer load/zoom
                    page = doc[i]
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)
                    samples = bytes(pix.samples)
                    page_sig.emit(
                        (render_id, i, samples, pix.width, pix.height, pix.stride)
                    )
            finally:
                doc.close()

        threading.Thread(target=_worker, daemon=True).start()

    def release(self) -> None:
        """Release resources — called by main_window on navigation away."""
        self._save_scroll_state()
        self._render_id += 1  # cancel any in-flight render
        self._clear_pages()

    def clear_session_state(self) -> None:
        """Reset all per-file session state (called when root changes)."""
        self._state_cache.clear()

    # ------------------------------------------------------------------
    # Probe / page signal handlers (main thread)
    # ------------------------------------------------------------------

    def _on_probe_done(self, data: object) -> None:
        render_id = data[0]  # type: ignore[index]
        if render_id != self._render_id:
            return  # stale render

        if data[1] == "permission":  # type: ignore[index]
            self._clear_pages()
            self.load_failed.emit("permission")
            return
        if data[1] == "access":  # type: ignore[index]
            self._clear_pages()
            self.load_failed.emit("access")
            return

        # data = (render_id, "ok", n_pages, page_w, zoom, saved_scroll)
        _, _, n_pages, page_w, zoom, saved_scroll = data  # type: ignore[misc]

        self._n_pages = n_pages
        self._first_page_width = page_w
        self._zoom_level = zoom

        # Replace "Loading…" with blank placeholder boxes
        self._clear_pages()
        for _ in range(n_pages):
            lbl = QLabel()
            lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            lbl.setStyleSheet("border: 1px solid #cccccc; background: white;")
            self._pages_layout.addWidget(
                lbl, alignment=Qt.AlignmentFlag.AlignHCenter
            )
            self._page_labels.append(lbl)

        pct = int(zoom * 100)
        self._zoom_label.setText(f"{pct}%")
        self._page_label.setText(f"{n_pages} page{'s' if n_pages != 1 else ''}")
        self._zoom_in_btn.setEnabled(zoom < _ZOOM_MAX)
        self._zoom_out_btn.setEnabled(zoom > _ZOOM_MIN)

        if saved_scroll is not None:
            QTimer.singleShot(
                0, lambda: self._restore_scroll(saved_scroll[0], saved_scroll[1])
            )

    def _on_page_ready(self, data: object) -> None:
        render_id, idx, samples, w, h, stride = data  # type: ignore[misc]
        if render_id != self._render_id:
            return  # stale render — discard

        qi = QImage(samples, w, h, stride, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qi)
        if idx < len(self._page_labels):
            self._page_labels[idx].setPixmap(pixmap)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def _zoom_in(self) -> None:
        self._zoom_level = min(self._zoom_level * _ZOOM_STEP, _ZOOM_MAX)
        self._rerender()

    def _zoom_out(self) -> None:
        self._zoom_level = max(self._zoom_level / _ZOOM_STEP, _ZOOM_MIN)
        self._rerender()

    def _fit_width(self) -> None:
        if self._first_page_width > 0:
            pane_w = max(self._scroll_area.viewport().width() - 20, 1)
            self._zoom_level = max(
                _ZOOM_MIN, min(_ZOOM_MAX, pane_w / self._first_page_width)
            )
        self._rerender()

    def _rerender(self) -> None:
        """Re-render the current document at the new zoom level (background thread)."""
        if self._current_path is None or not _FITZ_AVAILABLE or self._n_pages == 0:
            return

        self._render_id += 1
        render_id = self._render_id
        zoom = self._zoom_level
        n_pages = self._n_pages
        path_str = str(self._current_path)
        page_sig = self._page_ready

        # Swap in fresh blank placeholders at new zoom so old pages disappear
        self._clear_pages()
        for _ in range(n_pages):
            lbl = QLabel()
            lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            lbl.setStyleSheet("border: 1px solid #cccccc; background: white;")
            self._pages_layout.addWidget(
                lbl, alignment=Qt.AlignmentFlag.AlignHCenter
            )
            self._page_labels.append(lbl)

        pct = int(zoom * 100)
        self._zoom_label.setText(f"{pct}%")
        self._zoom_in_btn.setEnabled(zoom < _ZOOM_MAX)
        self._zoom_out_btn.setEnabled(zoom > _ZOOM_MIN)

        def _worker() -> None:
            try:
                doc = fitz.open(path_str)
            except Exception:
                return
            try:
                for i in range(n_pages):
                    if render_id != self._render_id:
                        return
                    page = doc[i]
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)
                    samples = bytes(pix.samples)
                    page_sig.emit(
                        (render_id, i, samples, pix.width, pix.height, pix.stride)
                    )
            finally:
                doc.close()

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_pages(self) -> None:
        self._page_labels = []
        while self._pages_layout.count():
            item = self._pages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ------------------------------------------------------------------
    # Scroll state
    # ------------------------------------------------------------------

    def _save_scroll_state(self) -> None:
        if self._current_path is None:
            return
        sb_h = self._scroll_area.horizontalScrollBar()
        sb_v = self._scroll_area.verticalScrollBar()
        self._state_cache[str(self._current_path)] = _PDFState(
            zoom=self._zoom_level,
            scroll_x=sb_h.value(),
            scroll_y=sb_v.value(),
        )

    def _restore_scroll(self, x: int, y: int) -> None:
        self._scroll_area.horizontalScrollBar().setValue(x)
        self._scroll_area.verticalScrollBar().setValue(y)

    def _show_message(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: gray;")
        self._pages_layout.addWidget(lbl)
