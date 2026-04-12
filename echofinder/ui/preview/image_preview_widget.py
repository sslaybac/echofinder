"""Image preview widget (US-036, US-037, US-038).

Renders images via Pillow at a pane-appropriate resolution.  Zoom in/out is
supported; zoom level is retained per file for the session duration.  The
previously loaded image resource is released when a new file is loaded.
"""

from __future__ import annotations

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
    from PIL import Image as _PILImage

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# Zoom step multiplier and limits
_ZOOM_STEP = 1.414213562  # √2 — each step doubles/halves area
_ZOOM_MIN = 0.0625        # 1/16 natural size
_ZOOM_MAX = 16.0          # 16× natural size


class ImagePreviewWidget(QWidget):
    """Preview widget for image files."""

    load_failed = pyqtSignal(str)  # "permission" | "access"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Per-file zoom cache: str(path) → zoom_level
        self._zoom_cache: dict[str, float] = {}
        self._current_path: Path | None = None
        self._zoom_level: float = 1.0   # 1.0 = 1 image pixel per screen pixel
        self._pil_image = None           # held in memory while this file is active

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar: zoom controls
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(6, 2, 6, 2)
        toolbar_layout.setSpacing(4)

        self._zoom_out_btn = QPushButton("−")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_out_btn.setToolTip("Zoom out")
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_in_btn.setToolTip("Zoom in")
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedWidth(40)
        self._fit_btn.setToolTip("Fit image to pane")
        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(52)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        toolbar_layout.addWidget(self._zoom_out_btn)
        toolbar_layout.addWidget(self._zoom_label)
        toolbar_layout.addWidget(self._zoom_in_btn)
        toolbar_layout.addWidget(self._fit_btn)
        toolbar_layout.addStretch()

        layout.addWidget(toolbar)

        # Scroll area holds the image label
        self._scroll_area = QScrollArea()
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._scroll_area.setWidget(self._image_label)

        layout.addWidget(self._scroll_area)

        # Connect toolbar buttons
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._fit_btn.clicked.connect(self._fit_to_pane)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Load and display the image at *path*."""
        # Save zoom level for the previous file
        if self._current_path is not None:
            self._zoom_cache[str(self._current_path)] = self._zoom_level

        # Release previous image data
        self._pil_image = None

        self._current_path = path

        if not _PIL_AVAILABLE:
            self._image_label.setText("Pillow not available.")
            return

        try:
            img = _PILImage.open(path)
            img.load()  # Force full decode so the file handle can be released
            self._pil_image = img
        except PermissionError:
            self._pil_image = None
            self.load_failed.emit("permission")
            return
        except OSError:
            self._pil_image = None
            self.load_failed.emit("access")
            return

        # Restore or compute fit zoom
        if str(path) in self._zoom_cache:
            self._zoom_level = self._zoom_cache[str(path)]
        else:
            self._zoom_level = self._compute_fit_zoom()

        self._render()

    def release(self) -> None:
        """Release the in-memory image resource."""
        if self._current_path is not None:
            self._zoom_cache[str(self._current_path)] = self._zoom_level
        self._pil_image = None
        self._image_label.setPixmap(QPixmap())

    def clear_session_state(self) -> None:
        """Reset all per-file session state (called when root changes)."""
        self._zoom_cache.clear()

    # ------------------------------------------------------------------
    # Zoom actions
    # ------------------------------------------------------------------

    def _zoom_in(self) -> None:
        self._zoom_level = min(self._zoom_level * _ZOOM_STEP, _ZOOM_MAX)
        self._render()

    def _zoom_out(self) -> None:
        self._zoom_level = max(self._zoom_level / _ZOOM_STEP, _ZOOM_MIN)
        self._render()

    def _fit_to_pane(self) -> None:
        self._zoom_level = self._compute_fit_zoom()
        self._render()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _compute_fit_zoom(self) -> float:
        if self._pil_image is None:
            return 1.0
        img_w, img_h = self._pil_image.size
        if img_w == 0 or img_h == 0:
            return 1.0
        vp = self._scroll_area.viewport()
        pane_w = max(vp.width(), 1)
        pane_h = max(vp.height(), 1)
        return min(pane_w / img_w, pane_h / img_h)

    def _render(self) -> None:
        if self._pil_image is None:
            return

        img_w, img_h = self._pil_image.size
        render_w = max(1, int(img_w * self._zoom_level))
        render_h = max(1, int(img_h * self._zoom_level))

        # Pillow resize to the target render dimensions
        resized = self._pil_image.resize((render_w, render_h), _PILImage.LANCZOS)

        # Convert to QPixmap via QImage (RGBA to avoid format issues)
        resized = resized.convert("RGBA")
        data = resized.tobytes("raw", "RGBA")
        qi = QImage(data, render_w, render_h, render_w * 4, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qi)

        self._image_label.setPixmap(pixmap)
        self._image_label.resize(render_w, render_h)

        pct = int(self._zoom_level * 100)
        self._zoom_label.setText(f"{pct}%")
        self._zoom_out_btn.setEnabled(self._zoom_level > _ZOOM_MIN)
        self._zoom_in_btn.setEnabled(self._zoom_level < _ZOOM_MAX)

    # Re-render on resize when at fit zoom
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pil_image is not None:
            # Recompute and re-render only if we were previously at fit
            current_fit = self._compute_fit_zoom()
            if abs(self._zoom_level - current_fit) < 0.001:
                self._zoom_level = current_fit
                self._render()
