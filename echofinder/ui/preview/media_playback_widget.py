"""Video and audio playback widget (US-039, US-040, US-041).

Delegates playback to python-vlc.  VLC manages its own memory independently of
the Python process.  Controls available: play/pause, stop, seek, volume.
Playback position resets on navigation.

If VLC or python-vlc is not available, a plain informational message is shown.

Embedding notes (Linux/X11)
---------------------------
* ``--no-xlib`` is required because Qt already holds the Xlib thread lock.
  Without it VLC re-initialises Xlib threading, conflicts with Qt, and falls
  back through a slow GL → VDPAU → software chain while flooding stderr.
* ``--vout=xcb_x11`` forces the X11/XCB software video output, which is the
  only vout module that reliably embeds via ``set_xwindow()``.  The default
  GL-based output creates its own render surface and ignores the window handle.
* Do NOT set ``WA_NativeWindow`` on the video frame in ``__init__``.  The widget
  is constructed before it is parented into the QStackedWidget; setting the
  attribute on a parentless hierarchy causes Qt to create a decorated top-level
  X11 window, flooding the event loop and making the app non-responsive.
* ``_VideoFrame`` emits ``first_paint`` from its ``paintEvent`` override.
  ``paintEvent`` is Qt's contract that the backing X11 window is mapped and
  ready for drawing commands — and therefore ready for ``set_xwindow()``.
  Waiting for ``paintEvent`` rather than using ``QTimer.singleShot(0, ...)``
  eliminates the race between the XCB map request and VLC's first render.
* ``player.stop()`` is synchronous and blocks while VLC tears down its decode
  pipeline, including X11 surface cleanup on the video frame window.
  ``_on_frame_ready`` must not start a new player on the same window until
  the previous ``stop()`` has returned.  The strategy:
    1. ``stop_playback()`` issues ``stop()`` on a daemon thread and records it
       as ``self._stop_thread``.
    2. ``_on_frame_ready`` checks ``_stop_thread.is_alive()``.  If the stop is
       still running it reschedules itself via ``QTimer.singleShot(50, ...)``
       and returns without touching the player.  Once the stop is confirmed
       complete it creates a *fresh* MediaPlayer for the new file (so the old
       stopped player is never reused) and starts playback.
  This eliminates two distinct crash causes: (a) concurrent ``stop()`` /
  ``play()`` on the same MediaPlayer from different threads, and (b) two VLC
  pipelines racing on the same X11 window.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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


class _VideoFrame(QFrame):
    """QFrame that signals once (per arm() cycle) when its X11 window is mapped.

    ``paintEvent`` is the definitive Qt signal that a widget's backing window
    exists and is mapped — the compositing manager has committed it to the
    screen, so ``winId()`` will return a valid XCB/X11 handle.

    ``arm()`` resets the notification flag.  It must be called before each new
    media load so that ``first_paint`` fires again for the next file.
    """

    first_paint = pyqtSignal(int)  # emits winId() value

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._notified = False

    def arm(self) -> None:
        """Allow the next paintEvent to emit first_paint."""
        self._notified = False

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._notified:
            self._notified = True
            self.first_paint.emit(int(self.winId()))


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
        self._pending_path: Path | None = None
        # Thread that is currently running player.stop() — checked in
        # _on_frame_ready to prevent starting a new player on the same window
        # while the previous pipeline is still tearing down.
        self._stop_thread: threading.Thread | None = None

        # --no-xlib    : Qt owns the Xlib thread lock; VLC must not reinitialise it.
        # --avcodec-hw=none : disable hardware codec probing (removes VA-API noise).
        # --vout=xcb_x11   : force X11/XCB software vout; the only module that
        #                     embeds cleanly via set_xwindow().
        self._instance = _vlc.Instance(
            "--no-xlib",
            "--avcodec-hw=none",
            "--vout=xcb_x11",
        )
        self._player = self._instance.media_player_new()

        # Video frame — VLC renders into this widget.
        self._video_frame = _VideoFrame()
        self._video_frame.setStyleSheet("background-color: black;")
        self._video_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._video_frame.first_paint.connect(self._on_frame_ready)
        layout.addWidget(self._video_frame)

        # Controls bar
        controls = QWidget()
        controls.setFixedHeight(36)
        ctrl_layout = QHBoxLayout(controls)
        ctrl_layout.setContentsMargins(8, 2, 8, 2)
        ctrl_layout.setSpacing(6)

        self._play_pause_btn = QPushButton("\u25b6")
        self._play_pause_btn.setFixedWidth(32)
        self._play_pause_btn.setToolTip("Play / Pause")

        self._stop_btn = QPushButton("\u25a0")
        self._stop_btn.setFixedWidth(32)
        self._stop_btn.setToolTip("Stop")

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 1000)
        self._seek_slider.setToolTip("Seek")

        self._time_label = QLabel("0:00")
        self._time_label.setFixedWidth(48)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        vol_label = QLabel("\U0001f50a")
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Load and prepare the media file at *path*."""
        if not self._vlc_ready:
            return

        self._timer.stop()
        self._pending_path = path
        self._play_pause_btn.setText("\u23f8")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")

        # Arm the video frame so first_paint fires for this new file.
        # If the widget is already visible, force a repaint immediately;
        # otherwise paintEvent fires naturally when Qt shows the widget.
        self._video_frame.arm()
        self._video_frame.update()

    def stop_playback(self) -> None:
        """Stop playback — called by main_window when navigating away.

        Cancels any pending deferred load and runs player.stop() on a daemon
        thread so the main thread is never blocked during navigation.

        Only one stop thread is active at a time.  If a previous stop is still
        in progress we do not start a second one on the same player.
        """
        if not self._vlc_ready:
            return

        # Cancel any pending load (first_paint may not have fired yet).
        self._pending_path = None

        self._timer.stop()
        self._play_pause_btn.setText("\u25b6")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")

        # Guard: don't start a second concurrent stop() on the same player.
        if self._stop_thread is not None and self._stop_thread.is_alive():
            return

        # Capture the player reference at call-time.  _on_frame_ready may
        # replace self._player on the next event-loop tick.
        player = self._player
        self._stop_thread = threading.Thread(target=player.stop, daemon=True)
        self._stop_thread.start()

    # ------------------------------------------------------------------
    # paintEvent-driven playback start
    # ------------------------------------------------------------------

    def _on_frame_ready(self, win_id: int) -> None:
        """Called (on main thread) once the video frame's X11 window is mapped.

        Defers start if a previous stop() is still tearing down VLC's X11
        pipeline on the same window — starting a new pipeline before the old
        one finishes causes a libvlc segfault.
        """
        if self._pending_path is None:
            return  # navigated away before the window was mapped

        # If the previous stop() is still running, reschedule and return.
        # 50 ms is enough for VLC's decode-pipeline teardown on typical hardware.
        if self._stop_thread is not None and self._stop_thread.is_alive():
            QTimer.singleShot(50, lambda: self._on_frame_ready(win_id))
            return

        path = self._pending_path
        self._pending_path = None
        self._stop_thread = None

        # Create a fresh MediaPlayer for each file.  Never reuse a player that
        # has been through stop() — its internal state is not safe to reuse.
        self._player = self._instance.media_player_new()

        media = self._instance.media_new(str(path))
        self._player.set_media(media)

        if sys.platform.startswith("linux"):
            self._player.set_xwindow(win_id)
        elif sys.platform == "darwin":
            self._player.set_nsobject(win_id)
        elif sys.platform == "win32":
            self._player.set_hwnd(win_id)

        self._player.play()
        # Set volume after play() so VLC's audio output pipeline is initialised.
        self._player.audio_set_volume(self._volume_slider.value())
        self._timer.start()

    # ------------------------------------------------------------------
    # Control slots
    # ------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        if not self._vlc_ready:
            return
        if self._player.is_playing():
            self._player.pause()
            self._play_pause_btn.setText("\u25b6")
        else:
            self._player.play()
            self._play_pause_btn.setText("\u23f8")
            self._timer.start()

    def _on_stop(self) -> None:
        if not self._vlc_ready:
            return
        self._timer.stop()
        self._play_pause_btn.setText("\u25b6")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        if self._stop_thread is not None and self._stop_thread.is_alive():
            return
        player = self._player
        self._stop_thread = threading.Thread(target=player.stop, daemon=True)
        self._stop_thread.start()

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

        if state in (_vlc.State.Ended, _vlc.State.Stopped, _vlc.State.Error):
            self._timer.stop()
            self._play_pause_btn.setText("\u25b6")
            self._seek_slider.setValue(0)
            self._time_label.setText("0:00")
            return

        if not self._seek_slider.isSliderDown():
            pos = self._player.get_position()  # 0.0–1.0
            self._seek_slider.setValue(int(pos * 1000))

        ms = self._player.get_time()  # milliseconds
        if ms >= 0:
            secs = ms // 1000
            mins = secs // 60
            self._time_label.setText(f"{mins}:{secs % 60:02d}")
