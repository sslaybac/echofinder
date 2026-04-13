"""Video playback widget (US-039, US-041 video portion).

Uses python-vlc for playback. No video data is held in the Python process;
VLC manages its own memory. Playback resets when ``load()`` is called with a
new path (standard previewer behavior).

Controls: play/pause toggle, stop, seek slider, volume slider.

Platform behaviour
------------------
**X11 (Qt platform = xcb) and Windows**
    VLC's video output is embedded directly in the preview pane. The render
    surface is a ``QFrame`` (``self._surface``) whose native window handle is
    passed to the VLC media player via ``set_xwindow`` (Linux/X11) or
    ``set_hwnd`` (Windows) immediately before each ``play()`` call.
    ``WA_NativeWindow`` is applied lazily at that point — never at construction
    time — to avoid creating an X11 sub-window while the widget is dormant in
    the preview stack.

**Wayland (Qt platform = wayland)**
    VLC's ``set_xwindow()`` requires a genuine X11 window ID.  When Qt runs in
    Wayland mode, ``winId()`` returns a Wayland surface handle, not an X11 ID;
    VLC rejects it with "bad X11 window" and falls back to creating its own
    top-level window, which the compositor decorates as an independent window.
    The fix: detect Wayland via ``QGuiApplication.platformName()`` and skip
    ``set_xwindow`` entirely.  VLC is initialised with ``--no-xlib`` (VLC's own
    recommendation when X11 threading is not set up), avoiding the
    "Xlib not initialized for threads" error.  Video plays in VLC's own window;
    all playback controls (play/pause, stop, seek, volume) remain functional
    via the python-vlc API.  An informational label is shown in the preview
    surface area to explain the external-window behaviour.

Volume safety
-------------
``audio_set_volume()`` is only safe once ``State.Playing`` is reached. Volume
is applied by the poll timer on the first confirmed-playing tick via a
``_volume_dirty`` flag (same pattern as the audio widget).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
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
except Exception:
    _VLC_AVAILABLE = False

# How often to update the seek slider while playing (ms)
_POLL_INTERVAL_MS = 500


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as ``m:ss``."""
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _wayland_mode() -> bool:
    """Return True when Qt is running on the Wayland platform.

    Checked at play-time rather than at import time so that
    ``QGuiApplication`` is guaranteed to exist.
    """
    return QGuiApplication.platformName() == "wayland"


class VideoPreviewWidget(QWidget):
    """Plays video files using python-vlc.

    On X11 and Windows, VLC's video output is embedded inside the preview
    pane.  On Wayland, VLC creates its own window (embedding is not possible
    because Qt's Wayland surfaces do not carry valid X11 window IDs); all
    playback controls remain functional.

    VLC instance and media player are created lazily on the first ``load()``
    call.  No video data is held in the Python process; VLC manages its own
    memory.

    If python-vlc or libvlc is unavailable at import time, the widget renders
    in a disabled state and does not crash.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the layout; VLC instance is created lazily on first load.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)

        self._instance: object | None = None   # vlc.Instance
        self._player: object | None = None     # vlc.MediaPlayer
        self._media: object | None = None      # vlc.Media — kept alive to prevent GC crash
        self._current_path: Path | None = None
        self._duration_ms: int = 0
        self._updating_seek = False            # guard: suppress slider feedback loop
        self._volume_dirty = True              # sync volume on next confirmed Playing state

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Video render surface.
        # On X11/Windows: VLC draws directly into this widget's native window.
        # On Wayland: VLC uses its own window; this area shows an info label.
        #
        # WA_NativeWindow is NOT set here. Setting it at construction time
        # forces Qt to create a native X11 sub-window immediately; on Wayland
        # (even via XWayland) the compositor decorates it with a title bar,
        # produces edge flickering, and deadlocks the event loop — before any
        # video file is selected. The attribute is applied lazily inside
        # _attach_surface(), only when embedding is actually needed.
        self._surface = QFrame()
        self._surface.setStyleSheet("background-color: black;")
        self._surface.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Informational overlay shown in Wayland mode while video is playing.
        # Hidden by default; shown by _attach_surface() on Wayland.
        surface_layout = QVBoxLayout(self._surface)
        self._surface_label = QLabel(
            "Video is playing in a separate VLC window.\n"
            "(Wayland session — in-pane embedding is not available.)"
        )
        self._surface_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._surface_label.setStyleSheet("color: #888888;")
        self._surface_label.setWordWrap(True)
        self._surface_label.hide()
        surface_layout.addWidget(self._surface_label)

        layout.addWidget(self._surface, stretch=1)

        # Controls area beneath the video surface
        controls = QWidget()
        ctl = QVBoxLayout(controls)
        ctl.setContentsMargins(12, 8, 12, 8)
        ctl.setSpacing(6)

        # Time labels + seek slider
        seek_row = QHBoxLayout()
        self._time_label = QLabel("0:00")
        self._time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._time_label.setMinimumWidth(40)

        self._duration_label = QLabel("0:00")
        self._duration_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._duration_label.setMinimumWidth(40)

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 1000)
        self._seek_slider.setValue(0)
        self._seek_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._seek_slider.sliderMoved.connect(self._on_seek_moved)

        seek_row.addWidget(self._time_label)
        seek_row.addWidget(self._seek_slider)
        seek_row.addWidget(self._duration_label)
        ctl.addLayout(seek_row)

        # Playback buttons
        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.setSpacing(12)

        self._play_btn = QPushButton("Play")
        self._play_btn.setFixedWidth(80)
        self._play_btn.clicked.connect(self._on_play_pause)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setFixedWidth(80)
        self._stop_btn.clicked.connect(self._on_stop)

        btn_row.addWidget(self._play_btn)
        btn_row.addWidget(self._stop_btn)
        ctl.addLayout(btn_row)

        # Volume row
        vol_row = QHBoxLayout()
        vol_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vol_row.setSpacing(8)

        vol_label = QLabel("Volume:")
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(100)
        self._vol_slider.setFixedWidth(140)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)

        vol_row.addWidget(vol_label)
        vol_row.addWidget(self._vol_slider)
        ctl.addLayout(vol_row)

        # Error / status label
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: gray;")
        ctl.addWidget(self._status_label)

        layout.addWidget(controls)

        # Poll timer — updates seek slider and time label while playing
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll_playback)

        if not _VLC_AVAILABLE:
            self._status_label.setText("python-vlc is not available.")
            self._play_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Stop any current playback and prepare *path* for playback.

        Creates the VLC instance on the first call. On Wayland, the instance
        is created with ``--no-xlib`` (as recommended by libvlc when Xlib
        thread-safety has not been initialised by the host process). On X11
        and Windows, no extra flags are needed.

        The render surface handle is wired up in ``_on_play_pause`` right
        before each ``play()`` call, guaranteeing the widget is visible.

        Args:
            path: Absolute path to the video file to load.
        """
        self._stop_playback()
        self._surface_label.hide()
        self._current_path = path
        self._status_label.setText("")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        self._duration_ms = 0
        self._duration_label.setText("0:00")
        self._play_btn.setText("Play")
        self._volume_dirty = True

        if not _VLC_AVAILABLE:
            return

        try:
            if self._instance is None:
                if sys.platform.startswith("linux") and _wayland_mode():
                    # Wayland: no X11 embedding; --no-xlib prevents the
                    # "Xlib not initialized for threads" error that libvlc
                    # emits when the process has not called XInitThreads().
                    self._instance = _vlc.Instance("--no-xlib")  # type: ignore[union-attr]
                else:
                    # X11 or Windows: Xlib access needed for set_xwindow.
                    self._instance = _vlc.Instance()  # type: ignore[union-attr]

            if self._player is None:
                self._player = self._instance.media_player_new()  # type: ignore[union-attr]

            # Keep a reference to the Media object for the lifetime of playback.
            # VLC's C layer holds a raw pointer; if Python GC collects it the
            # pointer becomes dangling and the process crashes immediately.
            self._media = self._instance.media_new(str(path))  # type: ignore[union-attr]
            self._player.set_media(self._media)  # type: ignore[union-attr]
        except Exception as exc:
            self._status_label.setText(f"Could not load file: {exc}")
            self._play_btn.setEnabled(False)
            return

        self._play_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)

    def release(self) -> None:
        """Stop playback and release the current media without destroying VLC.

        Clears all UI fields, hides the Wayland info label, and drops the
        Python reference to the ``Media`` object. The VLC instance and player
        are retained for reuse on the next ``load()`` call.
        """
        self._stop_playback()
        self._surface_label.hide()
        self._media = None
        self._current_path = None
        self._status_label.setText("")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        self._duration_label.setText("0:00")
        self._play_btn.setText("Play")

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        """Toggle between play and pause.

        Calls ``_attach_surface()`` immediately before each ``play()`` so that
        the render surface handle is current and the widget is visible.
        """
        if self._player is None:
            return
        try:
            state = self._player.get_state()  # type: ignore[union-attr]
            if state == _vlc.State.Playing:  # type: ignore[union-attr]
                self._player.pause()  # type: ignore[union-attr]
                self._play_btn.setText("Play")
                self._timer.stop()
            else:
                self._attach_surface()
                self._player.play()  # type: ignore[union-attr]
                # Do NOT call audio_set_volume() here: the audio output is
                # created lazily by VLC on the first play() call. Volume is
                # applied by the poll timer once State.Playing is confirmed.
                self._play_btn.setText("Pause")
                self._timer.start()
        except Exception as exc:
            self._status_label.setText(f"Playback error: {exc}")

    def _on_stop(self) -> None:
        """Stop playback and reset the seek slider."""
        self._stop_playback()
        self._surface_label.hide()
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")

    def _on_seek_moved(self, value: int) -> None:
        """Seek to the slider position when the user drags it.

        Connected to ``QSlider.sliderMoved`` (not ``valueChanged``) to avoid
        triggering seeks from programmatic poll-timer updates.

        Args:
            value: Slider position in the range ``[0, 1000]``.
        """
        if self._player is None:
            return
        self._updating_seek = True
        try:
            self._player.set_position(value / 1000.0)  # type: ignore[union-attr]
        except Exception:
            pass
        finally:
            self._updating_seek = False

    def _on_volume_changed(self, value: int) -> None:
        """Apply the new volume level, or defer it if not yet playing.

        Args:
            value: New volume level in the range ``[0, 100]``.
        """
        if self._player is not None:
            try:
                state = self._player.get_state()  # type: ignore[union-attr]
                if state == _vlc.State.Playing:  # type: ignore[union-attr]
                    self._player.audio_set_volume(value)  # type: ignore[union-attr]
                else:
                    self._volume_dirty = True
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _attach_surface(self) -> None:
        """Wire the render surface to VLC, or show the Wayland info label.

        Called immediately before each ``play()`` invocation. The preview
        stack has already switched to this widget, so ``self._surface`` is
        visible.

        - **X11**: Applies ``WA_NativeWindow`` (lazily, to avoid creating an
          X11 sub-window while the widget is dormant), then passes
          ``winId()`` to ``set_xwindow()``.
        - **Windows**: Passes ``winId()`` to ``set_hwnd()``.
        - **Wayland**: Skips window-handle embedding entirely — Qt's Wayland
          surface handles are not valid X11 window IDs. VLC creates its own
          window. The info label is shown in the surface area to explain the
          behaviour.
        """
        if self._player is None:
            return

        if sys.platform == "win32":
            self._player.set_hwnd(int(self._surface.winId()))  # type: ignore[union-attr]
        elif sys.platform.startswith("linux") and not _wayland_mode():
            # X11 mode: apply WA_NativeWindow here (not at construction) so
            # no native sub-window is created while the widget is inactive.
            self._surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
            self._surface.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
            self._player.set_xwindow(int(self._surface.winId()))  # type: ignore[union-attr]
        else:
            # Wayland (or unrecognised platform): cannot embed.
            # VLC will open its own window; show the info label so the user
            # knows where to find the video.
            self._surface_label.show()

    def _stop_playback(self) -> None:
        """Stop the VLC player and halt the poll timer.

        Safe to call before ``load()`` has been called (player is ``None``).
        """
        self._timer.stop()
        if self._player is not None:
            try:
                self._player.stop()  # type: ignore[union-attr]
            except Exception:
                pass
        self._play_btn.setText("Play")

    def _poll_playback(self) -> None:
        """Update the seek slider and time label; stop the timer when playback ends.

        Called every ``_POLL_INTERVAL_MS`` ms. On the first ``State.Playing``
        tick, applies any pending volume change before updating position.

        Handles ``State.Stopped`` in addition to ``State.Ended`` so that
        closing VLC's external window (Wayland mode) is detected and the UI
        resets correctly without waiting for user interaction.
        """
        if self._player is None:
            return
        try:
            state = self._player.get_state()  # type: ignore[union-attr]

            # Terminal states: natural end, external window close, or error.
            if state in (                           # type: ignore[union-attr]
                _vlc.State.Ended,                   # type: ignore[union-attr]
                _vlc.State.Stopped,                 # type: ignore[union-attr]
                _vlc.State.Error,                   # type: ignore[union-attr]
            ):
                self._timer.stop()
                self._play_btn.setText("Play")
                self._surface_label.hide()
                self._seek_slider.setValue(0)
                self._time_label.setText("0:00")
                return

            if state != _vlc.State.Playing:  # type: ignore[union-attr]
                return

            # Audio output confirmed ready — apply any pending volume change.
            if self._volume_dirty:
                self._player.audio_set_volume(self._vol_slider.value())  # type: ignore[union-attr]
                self._volume_dirty = False

            if self._updating_seek:
                return

            # Update duration once it becomes available from the stream
            if self._duration_ms <= 0:
                dur = self._player.get_length()  # type: ignore[union-attr]
                if dur > 0:
                    self._duration_ms = dur
                    self._duration_label.setText(_fmt_ms(dur))

            pos_frac = self._player.get_position()  # type: ignore[union-attr]
            if pos_frac >= 0:
                self._seek_slider.setValue(int(pos_frac * 1000))
                if self._duration_ms > 0:
                    self._time_label.setText(_fmt_ms(int(pos_frac * self._duration_ms)))
        except Exception:
            pass
