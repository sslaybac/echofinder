"""Video and audio playback widget (US-039, US-040, US-041).

Delegates playback to python-vlc.  VLC manages its own memory independently of
the Python process.  Controls available: play/pause, stop, seek, volume.
Playback position resets on navigation.

If VLC or python-vlc is not available, a plain informational message is shown.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    import vlc as _vlc

    _VLC_AVAILABLE = True
except (ImportError, OSError):
    _vlc = None  # type: ignore[assignment]
    _VLC_AVAILABLE = False


class MediaPlaybackWidget(QWidget):
    """Preview widget for video and audio files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if not _VLC_AVAILABLE:
            lbl = QLabel(
                "Media playback requires VLC to be installed.\n\n"
                "Install VLC from https://www.videolan.org/ and restart Echofinder."
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: gray;")
            layout.addWidget(lbl)
            self._vlc_ready = False
            return

        self._vlc_ready = True

        # --no-xlib: Qt already owns the Xlib threading context; letting VLC
        #   reinitialise it causes the "Xlib not initialized for threads" error
        #   and forces VLC through a slow GL/VDPAU/software fallback chain.
        # --avcodec-hw=none: disables GPU codec probing (VA-API/VDPAU), which
        #   fails silently on most systems and floods stderr with libva errors.
        self._instance = _vlc.Instance("--no-xlib", "--avcodec-hw=none")
        self._player = self._instance.media_player_new()

        # Video frame — VLC renders into this widget
        self._video_frame = QFrame()
        self._video_frame.setStyleSheet("background-color: black;")
        self._video_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._video_frame)

        # Controls bar
        controls = QWidget()
        controls.setFixedHeight(36)
        ctrl_layout = QHBoxLayout(controls)
        ctrl_layout.setContentsMargins(8, 2, 8, 2)
        ctrl_layout.setSpacing(6)

        self._play_pause_btn = QPushButton("▶")
        self._play_pause_btn.setFixedWidth(32)
        self._play_pause_btn.setToolTip("Play / Pause")

        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedWidth(32)
        self._stop_btn.setToolTip("Stop")

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 1000)
        self._seek_slider.setToolTip("Seek")

        self._time_label = QLabel("0:00")
        self._time_label.setFixedWidth(48)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        vol_label = QLabel("🔊")
        vol_label.setFixedWidth(20)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(80)
        self._volume_slider.setToolTip("Volume")

        ctrl_layout.addWidget(self._play_pause_btn)
        ctrl_layout.addWidget(self._stop_btn)
        ctrl_layout.addWidget(self._seek_slider)
        ctrl_layout.addWidget(self._time_label)
        ctrl_layout.addWidget(vol_label)
        ctrl_layout.addWidget(self._volume_slider)

        layout.addWidget(controls)

        # Timer for position/state updates
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update_controls)

        # Signals
        self._play_pause_btn.clicked.connect(self._on_play_pause)
        self._stop_btn.clicked.connect(self._on_stop)
        self._seek_slider.sliderMoved.connect(self._on_seek)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        self._is_seeking = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Load and prepare the media file at *path*."""
        if not self._vlc_ready:
            return

        # Stop any current playback
        self._player.stop()
        self._timer.stop()

        media = self._instance.media_new(str(path))
        self._player.set_media(media)

        # Apply current volume setting now that a media is loaded
        self._player.audio_set_volume(self._volume_slider.value())

        self._play_pause_btn.setText("⏸")

        # Defer window attachment and play() to the next event-loop iteration.
        # The QStackedWidget has just switched to show this widget but the X11
        # window may not be mapped yet; winId() at this point returns a stale
        # or unregistered handle, causing "bad X11 window" errors in VLC.
        # A zero-delay singleShot yields control back to Qt so the compositor
        # commit happens before VLC tries to render into the frame.
        QTimer.singleShot(0, self._start_playback)

    def stop_playback(self) -> None:
        """Stop playback — called by main_window when navigating away."""
        if not self._vlc_ready:
            return
        self._player.stop()
        self._timer.stop()
        self._play_pause_btn.setText("▶")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")

    # ------------------------------------------------------------------
    # VLC attachment and deferred start
    # ------------------------------------------------------------------

    def _start_playback(self) -> None:
        """Attach the video window and begin playing (called via singleShot)."""
        self._attach_video_output()
        self._player.play()
        self._timer.start()

    def _attach_video_output(self) -> None:
        """Attach VLC video output to the video frame widget."""
        win_id = int(self._video_frame.winId())
        if sys.platform.startswith("linux"):
            self._player.set_xwindow(win_id)
        elif sys.platform == "darwin":
            self._player.set_nsobject(win_id)
        elif sys.platform == "win32":
            self._player.set_hwnd(win_id)

    # ------------------------------------------------------------------
    # Control slots
    # ------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        if not self._vlc_ready:
            return
        if self._player.is_playing():
            self._player.pause()
            self._play_pause_btn.setText("▶")
        else:
            self._player.play()
            self._play_pause_btn.setText("⏸")
            self._timer.start()

    def _on_stop(self) -> None:
        if not self._vlc_ready:
            return
        self._player.stop()
        self._timer.stop()
        self._play_pause_btn.setText("▶")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")

    def _on_seek(self, value: int) -> None:
        if not self._vlc_ready:
            return
        self._player.set_position(value / 1000.0)

    def _on_volume_changed(self, value: int) -> None:
        if not self._vlc_ready:
            return
        self._player.audio_set_volume(value)

    def _update_controls(self) -> None:
        """Periodic update: sync seek slider and time label with playback position."""
        if not self._vlc_ready:
            return

        state = self._player.get_state()

        # Auto-stop when media ends
        if state in (_vlc.State.Ended, _vlc.State.Stopped, _vlc.State.Error):
            self._timer.stop()
            self._play_pause_btn.setText("▶")
            self._seek_slider.setValue(0)
            self._time_label.setText("0:00")
            return

        # Update seek slider (skip if user is dragging it)
        if not self._seek_slider.isSliderDown():
            pos = self._player.get_position()  # 0.0–1.0
            self._seek_slider.setValue(int(pos * 1000))

        # Update time label
        ms = self._player.get_time()  # milliseconds
        if ms >= 0:
            secs = ms // 1000
            mins = secs // 60
            self._time_label.setText(f"{mins}:{secs % 60:02d}")
