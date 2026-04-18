"""Audio playback widget (US-040, US-041 audio portion).

Uses python-vlc for playback. No audio data is held in the Python process;
VLC manages its own memory. Playback resets when ``load()`` is called with a
new path (standard previewer behavior).

Controls: play/pause toggle, stop, seek slider, volume slider.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer

logger = logging.getLogger(__name__)
from PyQt6.QtWidgets import (
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
    """Format milliseconds as ``m:ss``.

    Args:
        ms: Duration in milliseconds.

    Returns:
        A string in ``m:ss`` format, e.g. ``"3:07"``.
    """
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class AudioPreviewWidget(QWidget):
    """Plays audio files using python-vlc with play/pause, stop, seek, and volume.

    The VLC instance and media player are created lazily on the first call to
    ``load()``. No audio data is held in the Python process; VLC manages its
    own memory. Playback resets when ``load()`` is called with a new path.

    If python-vlc or libvlc is unavailable at import time, the widget renders
    in a disabled state and does not crash.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the control layout; VLC instance is created lazily on first load.

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
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # File name label
        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(True)
        name_font = self._name_label.font()
        name_font.setPointSize(12)
        name_font.setBold(True)
        self._name_label.setFont(name_font)
        layout.addWidget(self._name_label)

        # Time labels + seek slider
        seek_row = QHBoxLayout()
        self._time_label = QLabel("0:00")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._time_label.setMinimumWidth(40)

        self._duration_label = QLabel("0:00")
        self._duration_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
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
        layout.addLayout(seek_row)

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
        layout.addLayout(btn_row)

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
        layout.addLayout(vol_row)

        # Error / status label
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: gray;")
        layout.addWidget(self._status_label)

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

        Resets the seek slider, time display, and play button to their initial
        state. The VLC instance and media player are created on the first call.
        Volume is not applied here; it is synced by ``_poll_playback`` once
        ``State.Playing`` is confirmed (libvlc VLC 3.x crash avoidance).

        Args:
            path: Absolute path to the audio file to load.
        """
        self._stop_playback()
        self._current_path = path
        self._name_label.setText(path.name)
        self._status_label.setText("")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        self._duration_ms = 0
        self._duration_label.setText("0:00")
        self._play_btn.setText("Play")
        self._volume_dirty = True  # re-sync volume on next confirmed Playing state

        if not _VLC_AVAILABLE:
            return

        try:
            if self._instance is None:
                # --no-video / --no-xlib: suppress video output and X11 init;
                # required to avoid a segfault when VLC runs inside a Qt app on Linux.
                self._instance = _vlc.Instance("--no-video", "--no-xlib")  # type: ignore[union-attr]
            if self._player is None:
                self._player = self._instance.media_player_new()  # type: ignore[union-attr]
                # Attach VLC error event listener (runs on VLC's internal thread;
                # only logging is safe here — no Qt API calls).
                em = self._player.event_manager()  # type: ignore[union-attr]
                em.event_attach(
                    _vlc.EventType.MediaPlayerEncounteredError,  # type: ignore[union-attr]
                    self._on_vlc_error,
                )

            # Keep a reference to the Media object for the lifetime of playback.
            # VLC's C layer holds a raw pointer; if Python GC collects the object
            # the pointer becomes dangling and the process crashes immediately.
            self._media = self._instance.media_new(str(path))  # type: ignore[union-attr]
            self._player.set_media(self._media)  # type: ignore[union-attr]
            # Do NOT call audio_set_volume() here: the VLC audio output is created
            # lazily on the first play() call, so calling it before play() crashes
            # with a null-pointer dereference inside libvlc (VLC 3.x bug).
            logger.debug("VLC media instance created for %s", path)
        except Exception as exc:
            logger.exception("VLC initialization error for %s", path)
            self._status_label.setText(f"Could not load file: {exc}")
            self._play_btn.setEnabled(False)
            return

        self._play_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)

    def release(self) -> None:
        """Stop playback and release the current media without destroying VLC.

        Clears all UI fields and drops the Python reference to the ``Media``
        object. The VLC instance and player are retained so they can be reused
        on the next ``load()`` call without re-initializing libvlc.
        """
        if self._current_path is not None:
            logger.debug("VLC media instance released for %s", self._current_path)
        self._stop_playback()
        self._media = None   # release Python reference; VLC player retains its own
        self._current_path = None
        self._name_label.setText("")
        self._status_label.setText("")
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        self._duration_label.setText("0:00")
        self._play_btn.setText("Play")

    def shutdown(self) -> None:
        """Release VLC player and instance; call once during application exit.

        libvlc registers an atexit handler that joins its internal threads.
        If the instance is not explicitly released before Python's cleanup runs,
        that join can block indefinitely. Release order must be: stop → media
        release → player release → instance release.
        """
        self._timer.stop()
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
        self._media = None
        if self._player is not None:
            try:
                self._player.release()
            except Exception:
                pass
            self._player = None
        if self._instance is not None:
            try:
                self._instance.release()
            except Exception:
                pass
            self._instance = None
        logger.debug("VLC instance released on shutdown")

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        """Toggle between play and pause; connected to the Play/Pause button.

        If the player is currently in ``State.Playing``, pauses it and stops
        the poll timer. Otherwise calls ``play()`` and starts the timer.
        Volume is synced by ``_poll_playback`` on the first confirmed-playing
        tick rather than here, because ``play()`` is asynchronous and the
        audio output is not ready until ``State.Playing`` is reached.
        """
        if self._player is None:
            return
        try:
            state = self._player.get_state()  # type: ignore[union-attr]
            playing = state in (
                _vlc.State.Playing,  # type: ignore[union-attr]
            )
            if playing:
                self._player.pause()  # type: ignore[union-attr]
                self._play_btn.setText("Play")
                self._timer.stop()
            else:
                self._player.play()  # type: ignore[union-attr]
                # Do NOT call audio_set_volume() here: play() is asynchronous and
                # the audio output is not yet ready. Volume is synced by the poll
                # timer once State.Playing is confirmed.
                self._play_btn.setText("Pause")
                self._timer.start()
        except Exception as exc:
            self._status_label.setText(f"Playback error: {exc}")

    def _on_stop(self) -> None:
        """Stop playback and reset the seek slider to the beginning.

        Connected to the Stop button. Delegates to ``_stop_playback`` and
        then resets the position UI so the next press of Play starts from
        the beginning of the file.
        """
        self._stop_playback()
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")

    def _on_seek_moved(self, value: int) -> None:
        """Seek to the position indicated by the slider when the user drags it.

        Connected to ``QSlider.sliderMoved`` (not ``valueChanged``) so that
        programmatic updates from the poll timer do not trigger a seek. Sets
        ``_updating_seek`` while the VLC call is in progress to suppress the
        poll timer's position update for that tick.

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
        """Apply the new volume level from the slider.

        ``audio_set_volume()`` is only safe once the VLC audio output exists,
        which is not until ``State.Playing`` is reached. If the player is in
        any other state, ``_volume_dirty`` is set so that ``_poll_playback``
        applies the change on the next confirmed-playing tick.

        Args:
            value: New volume level in the range ``[0, 100]``.
        """
        # Only safe to call once the audio output exists (State.Playing).
        # When paused or stopped, the change is picked up by _poll_playback
        # on the next play() via _volume_dirty.
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

    def _on_vlc_error(self, event) -> None:
        """Log VLC error events received from the VLC event manager.

        Called on VLC's internal thread — must not access any Qt objects.
        """
        logger.warning("VLC error event: %s", event.type)

    def _stop_playback(self) -> None:
        """Stop the VLC player and halt the poll timer.

        Safe to call at any point, including before ``load()`` has been called
        (player is ``None``). Resets the Play button label but does not clear
        the seek slider or time label; callers are responsible for that if
        needed.
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

        Called every ``_POLL_INTERVAL_MS`` milliseconds while the play timer
        is active. Exits early for any state other than ``State.Playing`` or
        ``State.Ended``. On the first ``State.Playing`` tick, applies any
        pending volume change via ``_volume_dirty`` before updating position.
        """
        if self._player is None:
            return
        try:
            state = self._player.get_state()  # type: ignore[union-attr]
            if state == _vlc.State.Ended:  # type: ignore[union-attr]
                self._timer.stop()
                self._play_btn.setText("Play")
                self._seek_slider.setValue(0)
                self._time_label.setText("0:00")
                return

            if state != _vlc.State.Playing:  # type: ignore[union-attr]
                return

            # Audio output is confirmed ready — apply any pending volume change.
            if self._volume_dirty:
                self._player.audio_set_volume(self._vol_slider.value())  # type: ignore[union-attr]
                self._volume_dirty = False

            if self._updating_seek:
                return

            # Update duration once it becomes available
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
