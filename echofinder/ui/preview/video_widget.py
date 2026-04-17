"""Video playback widget (US-039, US-041 video portion).

Uses Qt Multimedia (``QMediaPlayer`` + ``QVideoWidget``) for in-pane video
playback. Qt renders video through its own pipeline (FFmpeg backend on Linux,
Media Foundation / DirectShow on Windows), so no platform-specific window
handle wiring is required and Wayland sessions are fully supported.

python-vlc was evaluated first. VLC 3.x has no stable API for Wayland surface
embedding: ``set_xwindow()`` requires a genuine X11 window ID, but Qt in
Wayland mode provides a Wayland surface handle that VLC rejects. Qt Multimedia
does not have this limitation — it composes video through Qt's own render loop.

Controls: play/pause toggle, stop, seek slider, volume slider.

Volume notes
------------
``QAudioOutput.setVolume()`` accepts a float in ``[0.0, 1.0]``. The volume
slider operates in the integer range ``[0, 100]``; division by 100 converts
between the two.

Seek notes
----------
Position is tracked via ``QMediaPlayer.positionChanged`` (milliseconds).
The seek slider range is ``[0, 1000]`` (same convention as the audio widget).
``QSlider.isSliderDown()`` guards against a feedback loop when the poll
update and the user drag occur simultaneously.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl

logger = logging.getLogger(__name__)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# How often Qt Multimedia signals position changes while playing (driven by
# QMediaPlayer.positionChanged — no poll timer needed).
_SEEK_SLIDER_RANGE = 1000


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as ``m:ss``."""
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class VideoPreviewWidget(QWidget):
    """Plays video files using Qt Multimedia with in-pane video rendering.

    ``QVideoWidget`` is used as the render surface.  Qt routes video through
    its own backend (FFmpeg on Linux, Media Foundation on Windows) without
    requiring native window handle injection, so the widget works correctly
    on both X11 and Wayland sessions.

    Playback state is tracked via Qt signals; no poll timer is needed.
    Playback resets when ``load()`` is called with a new path.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the widget; ``QMediaPlayer`` is created immediately.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)

        self._duration_ms: int = 0

        # Qt Multimedia objects
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._audio_out.setVolume(1.0)
        self._player.setAudioOutput(self._audio_out)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # QVideoWidget renders video in-pane on all Qt-supported platforms,
        # including Wayland, without any native window handle wiring.
        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._player.setVideoOutput(self._video_widget)
        layout.addWidget(self._video_widget, stretch=1)

        # Controls area
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
        self._seek_slider.setRange(0, _SEEK_SLIDER_RANGE)
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

        # Wire Qt Multimedia signals
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error_occurred)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Stop any current playback and prepare *path* for playback.

        Resets the seek slider, time display, and play button. The player
        begins buffering immediately; the user must press Play to start
        playback.

        Args:
            path: Absolute path to the video file to load.
        """
        self._player.stop()
        self._duration_ms = 0
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        self._duration_label.setText("0:00")
        self._play_btn.setText("Play")
        self._status_label.setText("")

        self._player.setSource(QUrl.fromLocalFile(str(path)))
        logger.debug("Video source set: %s", path)

    def release(self) -> None:
        """Stop playback and clear the media source.

        Called when the user navigates to a different file or the preview
        pane is cleared.  Drops the media reference and resets all UI fields.
        """
        logger.debug("Video source released")
        self._player.stop()
        self._player.setSource(QUrl())   # clear source
        self._duration_ms = 0
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00")
        self._duration_label.setText("0:00")
        self._play_btn.setText("Play")
        self._status_label.setText("")

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        """Toggle between play and pause."""
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_stop(self) -> None:
        """Stop playback and reset position to the beginning."""
        self._player.stop()

    def _on_seek_moved(self, value: int) -> None:
        """Seek to the slider position when the user drags it.

        Connected to ``QSlider.sliderMoved`` (not ``valueChanged``) so that
        programmatic updates from ``_on_position_changed`` do not trigger a
        seek.

        Args:
            value: Slider position in the range ``[0, 1000]``.
        """
        if self._duration_ms > 0:
            target_ms = int(value / _SEEK_SLIDER_RANGE * self._duration_ms)
            self._player.setPosition(target_ms)

    def _on_volume_changed(self, value: int) -> None:
        """Apply the new volume level from the slider.

        Args:
            value: New volume level in the range ``[0, 100]``.
        """
        self._audio_out.setVolume(value / 100.0)

    def _on_playback_state_changed(
        self, state: QMediaPlayer.PlaybackState
    ) -> None:
        """Update the play/pause button label to match playback state."""
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("Pause")
        else:
            self._play_btn.setText("Play")

    def _on_position_changed(self, position_ms: int) -> None:
        """Update the seek slider and elapsed time label.

        Skips the update while the user is dragging the slider to prevent
        the position signal from snapping the slider back mid-drag.

        Args:
            position_ms: Current playback position in milliseconds.
        """
        if self._seek_slider.isSliderDown():
            return
        self._time_label.setText(_fmt_ms(position_ms))
        if self._duration_ms > 0:
            self._seek_slider.setValue(
                int(position_ms / self._duration_ms * _SEEK_SLIDER_RANGE)
            )

    def _on_duration_changed(self, duration_ms: int) -> None:
        """Update the total duration label when the stream reports its length.

        Args:
            duration_ms: Total media duration in milliseconds.
        """
        self._duration_ms = duration_ms
        self._duration_label.setText(_fmt_ms(duration_ms))

    def _on_media_status_changed(
        self, status: QMediaPlayer.MediaStatus
    ) -> None:
        """Reset UI on end of media.

        Args:
            status: The new media status value.
        """
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._seek_slider.setValue(0)
            self._time_label.setText("0:00")

    def _on_error_occurred(
        self, error: QMediaPlayer.Error, error_string: str
    ) -> None:
        """Display playback errors in the status label.

        Args:
            error: The error code enum value.
            error_string: Human-readable description of the error.
        """
        if error != QMediaPlayer.Error.NoError:
            logger.warning("Video playback error (%s): %s", error, error_string)
            self._status_label.setText(f"Playback error: {error_string}")
