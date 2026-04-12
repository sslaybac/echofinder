"""PDF preview widget (US-042, US-043, US-044).

Renders PDFs via PyMuPDF (fitz) at pane-appropriate resolution.  Supports
multi-page scrolling and per-file zoom state retained for the session.  The
open document is released when a new file is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
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

    load_failed = pyqtSignal(str)  # "permission" | "access"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._state_cache: dict[str, _PDFState] = {}
        self._current_path: Path | None = None
        self._zoom_level: float = 1.0
        self._doc = None  # fitz.Document | None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 2, 6, 2)
        tb_layout.setSpacing(4)

        self._zoom_out_btn = QPushButton("−")
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
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._pages_container = QWidget()
        self._pages_layout = QVBoxLayout(self._pages_container)
        self._pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._pages_layout.setSpacing(8)
        self._pages_layout.setContentsMargins(8, 8, 8, 8)
        self._scroll_area.setWidget(self._pages_container)

        layout.addWidget(self._scroll_area)

        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._fit_btn.clicked.connect(self._fit_width)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Open and render the PDF at *path*."""
        self._save_scroll_state()
        self._close_document()

        self._current_path = path

        if not _FITZ_AVAILABLE:
            self._show_message("PyMuPDF not available.")
            return

        try:
            doc = fitz.open(str(path))
        except PermissionError:
            self.load_failed.emit("permission")
            return
        except Exception:
            self.load_failed.emit("access")
            return

        self._doc = doc

        # Restore or initialise per-file state
        saved = self._state_cache.get(str(path))
        if saved is not None:
            self._zoom_level = saved.zoom
        else:
            self._zoom_level = self._compute_fit_zoom()

        self._render_all_pages()

        # Restore scroll position after pages are rendered
        if saved is not None:
            # Deferred restore because layout may not be committed yet
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._restore_scroll(saved.scroll_x, saved.scroll_y))

    def release(self) -> None:
        """Release the open document resource."""
        self._save_scroll_state()
        self._close_document()
        self._clear_pages()

    def clear_session_state(self) -> None:
        """Reset all per-file session state (called when root changes)."""
        self._state_cache.clear()

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def _zoom_in(self) -> None:
        self._zoom_level = min(self._zoom_level * _ZOOM_STEP, _ZOOM_MAX)
        self._render_all_pages()

    def _zoom_out(self) -> None:
        self._zoom_level = max(self._zoom_level / _ZOOM_STEP, _ZOOM_MIN)
        self._render_all_pages()

    def _fit_width(self) -> None:
        self._zoom_level = self._compute_fit_zoom()
        self._render_all_pages()

    def _compute_fit_zoom(self) -> float:
        """Compute zoom such that the first page fills the pane width."""
        if self._doc is None or len(self._doc) == 0:
            return 1.0
        page = self._doc[0]
        page_w_pts = page.rect.width
        pane_w = max(self._scroll_area.viewport().width() - 20, 1)
        if page_w_pts == 0:
            return 1.0
        return pane_w / page_w_pts

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_all_pages(self) -> None:
        if self._doc is None:
            return

        self._clear_pages()
        n_pages = len(self._doc)
        self._page_label.setText(f"{n_pages} page{'s' if n_pages != 1 else ''}")

        for i in range(n_pages):
            pixmap = self._render_page(i)
            lbl = QLabel()
            lbl.setPixmap(pixmap)
            lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            lbl.setStyleSheet("border: 1px solid #cccccc; background: white;")
            self._pages_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        pct = int(self._zoom_level * 100)
        self._zoom_label.setText(f"{pct}%")
        self._zoom_in_btn.setEnabled(self._zoom_level < _ZOOM_MAX)
        self._zoom_out_btn.setEnabled(self._zoom_level > _ZOOM_MIN)

    def _render_page(self, page_num: int) -> QPixmap:
        page = self._doc[page_num]
        mat = fitz.Matrix(self._zoom_level, self._zoom_level)
        pix = page.get_pixmap(matrix=mat)
        qi = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(qi)

    def _clear_pages(self) -> None:
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _close_document(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None

    def _show_message(self, text: str) -> None:
        self._clear_pages()
        lbl = QLabel(text)
        lbl.setStyleSheet("color: gray;")
        self._pages_layout.addWidget(lbl)
