"""Image preview widget (US-036, US-037, US-038).

Design notes:
- Images are loaded from bytes (no filesystem I/O here).
- Zoom level is retained per file path for the session; resets on relaunch.
- Only one pixmap is held in memory at a time; navigating away calls release().
- Ctrl+Scroll or +/- keys change zoom; unmodified scroll pans the image.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeyEvent, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_ZOOM_STEP = 1.25   # multiplicative step per zoom action
_ZOOM_MIN = 0.05
_ZOOM_MAX = 16.0


class ImagePreviewWidget(QWidget):
    """Renders an image scaled to fit the pane; supports per-file zoom retention."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidgetResizable(False)
        layout.addWidget(self._scroll)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._label)

        # Per-file session zoom state: path_str → zoom factor
        # zoom=1.0 means "fit to pane"; >1 zooms in, <1 zooms out beyond fit.
        self._zoom_state: dict[str, float] = {}

        self._pixmap: QPixmap | None = None
        self._current_path: str | None = None

        # Intercept wheel events on the viewport to separate Ctrl+Scroll (zoom)
        # from plain scroll (pan).
        self._scroll.viewport().installEventFilter(self)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path, image_bytes: bytes) -> None:
        """Display *image_bytes*; retain per-session zoom for *path*."""
        # Release previous image before loading the new one
        self._pixmap = None

        path_key = str(path)
        self._current_path = path_key

        px = QPixmap()
        if not px.loadFromData(image_bytes):
            self._label.setText("Unable to render this image.")
            self._label.setPixmap(QPixmap())
            return

        self._pixmap = px
        self._refresh()

    def release(self) -> None:
        """Release the held pixmap; called when navigating away from an image."""
        self._pixmap = None
        self._label.clear()
        self._current_path = None

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh()

    def keyPressEvent(self, event: QKeyEvent) -> None:
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
            self._apply_zoom(1.0)  # reset to fit
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Intercept Ctrl+Scroll on the scroll area viewport for zoom."""
        if obj is self._scroll.viewport() and isinstance(event, QWheelEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._zoom_by(_ZOOM_STEP)
                elif delta < 0:
                    self._zoom_by(1.0 / _ZOOM_STEP)
                return True  # consume; don't scroll
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _zoom_by(self, factor: float) -> None:
        key = self._current_path or ""
        current = self._zoom_state.get(key, 1.0)
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, current * factor))
        self._apply_zoom(new_zoom)

    def _apply_zoom(self, zoom: float) -> None:
        if self._current_path is not None:
            self._zoom_state[self._current_path] = zoom
        self._refresh()

    def _refresh(self) -> None:
        """Recompute the scaled pixmap from the current zoom and pane size."""
        if self._pixmap is None or self._pixmap.isNull():
            return

        viewport = self._scroll.viewport()
        avail_w = viewport.width()
        avail_h = viewport.height()
        if avail_w <= 0 or avail_h <= 0:
            return

        img_w = self._pixmap.width()
        img_h = self._pixmap.height()
        if img_w <= 0 or img_h <= 0:
            return

        # Scale that fits the image inside the viewport exactly
        fit_scale = min(avail_w / img_w, avail_h / img_h)

        zoom = self._zoom_state.get(self._current_path or "", 1.0)
        display_scale = fit_scale * zoom

        scaled_w = max(1, int(img_w * display_scale))
        scaled_h = max(1, int(img_h * display_scale))

        scaled = self._pixmap.scaled(
            scaled_w,
            scaled_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())
