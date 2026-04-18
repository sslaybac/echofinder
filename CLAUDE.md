# Echofinder

## Package Management
Use `uv` exclusively for all Python package management. Never use pip directly.

- Add dependencies: `uv add <package>`
- Remove dependencies: `uv remove <package>`
- Run the app: `uv run python -m echofinder`
- Run tests: `uv run pytest`

## Project Structure
Model-View separation is a hard requirement. Never put UI logic in the data layer or data
logic in widgets.

- `echofinder/models/` — data layer: `FileNode`, `HashCache`, `SessionState`, scanner
- `echofinder/services/` — business logic: hashing engine, file type resolver, file
  operations, polling engine, preview loader
- `echofinder/ui/` — all PyQt6 widgets and UI components

## Implementation Status
Eleven-stage plan. **All stages complete.**

| Stage | Title                        | Status    |
|-------|------------------------------|-----------|
| 1     | Skeleton and File Tree       | Complete  |
| 2     | Hashing and Cache            | Complete  |
| 3     | Duplicate Detection          | Complete  |
| 4     | Preview Pane (Core)          | Complete  |
| 5     | Metadata Panel               | Complete  |
| 6     | File Operations              | Complete  |
| 7     | Live Polling and Polish      | Complete  |
| 8     | PDF Preview                  | Complete  |
| 9     | Audio Playback               | Complete  |
| 10    | Video Playback               | Complete  |
| 11    | EPUB Preview                 | Complete  |

Stage 11 was added after the Staging Plan v2 was written; the staging plan does not
include it. The original spec is in `planning/Echofinder_Stage11_Context.md` (local
only — not in the repository).

Stages 9 and 10 use python-vlc. Stage 9 installed the dependency; Stage 10 reuses it.
VLC must be installed on the host system. Embedding VLC video output in a Qt widget
requires platform-specific wiring (`win_id` / `x_window` handle) — test on both Alma
Linux 9 and Windows 11.

## Stage 9: Audio Playback — Implementation Notes
`ui/preview/audio_widget.py` — `AudioPreviewWidget` with play/pause toggle, stop,
seek slider with elapsed/total time display, and volume slider.

**VLC initialisation** — `vlc.Instance` and `MediaPlayer` are created lazily on the
first `load()` call with `--no-video --no-xlib` to suppress video-output and X11
initialisation that would segfault inside a Qt application on Linux. The `Media`
object is stored as `self._media` for the lifetime of playback; releasing it to GC
while VLC holds a raw C pointer causes an immediate crash.

**Volume safety** — `audio_set_volume()` is only safe once the VLC audio output
exists, which is not until `State.Playing` is reached. It must not be called in
`load()` or immediately after `play()` (both crash with a null-pointer dereference
in libvlc 3.x). Volume is applied by the poll timer on the first confirmed-playing
tick via a `_volume_dirty` flag.

## Stage 10: Video Playback — Implementation Notes
`ui/preview/video_widget.py` — `VideoPreviewWidget` with play/pause toggle, stop,
seek slider with elapsed/total time display, and volume slider.

**Technology** — Uses `QMediaPlayer` + `QVideoWidget` + `QAudioOutput` from
`PyQt6.QtMultimedia` / `PyQt6.QtMultimediaWidgets` (included in PyQt6; no additional
package). Qt routes video through its own backend (FFmpeg 7.x on this machine) without
native window handle injection. `QVideoWidget` renders in-pane on both X11 and Wayland
sessions without any platform-specific wiring.

**Why not python-vlc** — VLC 3.x has no stable API for Wayland surface embedding.
`set_xwindow()` requires a genuine X11 window ID; Qt in Wayland mode provides a
Wayland surface handle that VLC rejects ("bad X11 window"), after which VLC falls back
to its own top-level OS window. Attempted workarounds (lazy `WA_NativeWindow`,
`--no-xlib`, platform detection + skip-embedding) all preserved the external-window
behaviour. Qt Multimedia resolves this completely because it composes video through
Qt's own render loop. python-vlc is retained for audio (Stage 9) where it works
correctly.

**Signal-based updates** — `QMediaPlayer.positionChanged` updates the seek slider and
time label; no poll timer is needed. `QMediaPlayer.durationChanged` updates the total
time display. `QMediaPlayer.playbackStateChanged` drives the play/pause button label.
`QMediaPlayer.errorOccurred` surfaces errors in the status label.

**Seek feedback guard** — `_on_position_changed` checks `QSlider.isSliderDown()`
before updating the slider, preventing the position signal from snapping the slider
back while the user is dragging.

**Volume** — `QAudioOutput.setVolume()` takes a float in `[0.0, 1.0]`; the slider's
integer `[0, 100]` range is divided by 100.

**Playback reset** — `release()` calls `player.stop()` then `player.setSource(QUrl())`
to clear the media reference before the preview pane switches widgets.

## Settled Design Decisions
The following decisions were made deliberately after evaluating alternatives. Do not
re-propose the rejected approaches. Rationale is in `planning/status_decisions_v8.pdf`.

**File type detection**
Use python-magic (libmagic) for MIME detection; fall back to file extension only when
magic fails. Extension-only detection was rejected as too brittle for misnamed or
extension-less files.

**Permission and ownership display**
Communicate all states through icon shape. Color is a reinforcing element only — never
the sole carrier of meaning. A text-color scheme was rejected on accessibility grounds
(red/yellow is the most common color blindness conflict).

**Write-only files**
Treat write-only files as `NOT_READABLE` — same padlock icon, no fourth state. The
application's primary operations (preview, hash, duplicate detection) all require read
access; a write-only file is functionally identical to an inaccessible one. Implemented
in `models/scanner._evaluate_permission()`.

**Symlink handling**
Display symlinks in the tree but do not read or hash them. The preview pane shows the
target path. If the target falls within the current root, provide a clickable jump link.
Following symlinks transparently was rejected (infinite loop risk, obscures structure).
Excluding them entirely was rejected (they are real filesystem entries).

**Duplicate indicators**
Two states only: general duplicate (copy icon) and specific duplicate (star-half icon,
shown on peers when a file is selected). Do not attempt to visually encode group
membership — groups can be arbitrarily large and the metadata panel's duplicate
submenu already provides precise group navigation.

**Unreadable files — slot-4 initial state**
Set `hash_state = HashState.UNIQUE` (no icon) at scan time for any file where
`permission == NOT_READABLE`. The hourglass would be shown permanently otherwise,
since unreadable files are never submitted to the hashing queue.
Implemented in `models/scanner._initial_hash_state()`.

**Hash cache invalidation**
Freshness requires an exact match on path, size, and mtime (via `os.stat()`). A
path-only key was rejected (wouldn't detect content changes). Rehashing on every
session load was rejected as impractical for large directories.

**Hashing thread pool**
Cap at `(CPU core count − 1)`. An uncapped pool was rejected (degrades UI
responsiveness). A fixed low cap was rejected in favour of a hardware-relative default.
A user-configurable cap is deferred to a future release.

**File deletion**
Send to system trash via send2trash. Permanent deletion was rejected due to
irreversible data loss risk.

**Rename**
Inline F2 editing via PyQt6 delegate/editor pattern. A separate rename dialog was
rejected; inline editing matches OS file manager convention. Collision detection follows
host OS rules (case-sensitive on Linux, case-insensitive on Windows). On collision:
`QApplication.beep()`, editor stays open, conflicting item's slot 4 shows exclamation
mark, status bar shows plain-language message.

**Copy operation**
Not supported and out of scope. The application is oriented toward reducing file
redundancy; copy works against that goal.

**Drag-and-drop scope**
Self-contained within the application only. Dragging into or out of the OS file manager
is not supported.

**File movement**
Three interaction paths (keyboard mode, drag-and-drop, move dialog) all share the same
underlying `move_item()` function and conflict-resolution logic. Divergent behavior
across paths was explicitly rejected as a design goal.

**Directory merge on partial failure**
Continue processing remaining files; collect all failures; report together at the end.
Stopping at the first error was rejected (a single inaccessible file should not block a
large merge). Rollback was rejected (rollback can itself fail).

**Text encoding cascade**
UTF-8 first (also covers ASCII) → charset-normalizer detection → Latin-1 fallback.
Latin-1 maps every possible byte value and always succeeds. UTF-16/UTF-32
suggestions from charset-normalizer are skipped to avoid false positives on short
content. The detected encoding name is displayed in the metadata panel.

**Hashing progress indicator**
`QProgressBar` + text label in the status bar. Label format: `Hashing… N / Total (N
from cache)`. Both widgets added at hashing start, removed on completion. A
dedicated layout slot above the file tree was rejected as too intrusive for a transient
background activity.

**View state persistence**
Tree expansion state and root directory are persisted to disk and restored on launch.
Image zoom, PDF scroll position, and PDF zoom are retained in memory for the session
only and reset on relaunch. Media playback position resets on navigation.

## Stage 11: EPUB Preview — Implementation Notes
`ui/preview/epub_widget.py` — `EpubPreviewWidget` with chapter navigation (Previous /
Next buttons + "Chapter N of M" label), Ctrl+scroll and keyboard zoom, and per-session
state retention.

**Dependencies** — `ebooklib` (EPUB parsing) and `PyQt6-WebEngine` (rendering). Both
are in `pyproject.toml`. On some Linux distributions an additional system package may
be required for WebEngine (e.g. `qt6-webengine`).

**Architecture** — `ebooklib` parses the EPUB spine; each spine item that is an
`EpubHtml` instance becomes a navigable chapter. `QWebEngineView` renders chapter HTML.
A module-level `_EpubSchemeHandler` singleton (installed once on
`QWebEngineProfile.defaultProfile()`) serves embedded assets (images, CSS, fonts) via
the custom scheme `echofinder-epub://book/<href>`, resolved from the in-memory book.
Assets are never written to disk.

**Scheme registration** — `QWebEngineUrlScheme.registerScheme()` must be called before
`QApplication()` is instantiated. This is handled in `__main__.py`.

**Optional dependency guard** — both `PyQt6.QtWebEngineWidgets` and `ebooklib` are
imported inside a `try/except ImportError` block. If either is absent,
`_WEBENGINE_AVAILABLE` is `False` and `EpubPreviewWidget` is replaced by a no-op stub
so the application never crashes on import. EPUB files fall back to the unsupported
widget in that case.

**Base URL for relative assets** — `setContent()` is called with a `base_url` of
`echofinder-epub://book/<chapter-directory>/` so that relative `src` and `href`
attributes in chapter HTML resolve correctly through the scheme handler.

**Session state** — chapter index, per-chapter scroll position, and zoom level are
retained in memory (keyed by file path) for the session; not persisted to disk. Scroll
position is saved via `window.scrollY` (JavaScript callback) before each chapter
navigation and restored in `_on_load_finished` after the new page loads.

**Zoom** — Ctrl+scroll wheel and `+`/`-` keys adjust `QWebEngineView.setZoomFactor()`
in multiplicative `_ZOOM_STEP = 1.1` increments, clamped to `[0.5, 3.0]`. Ctrl+0
resets to 1.0.

**Error handling** — any exception from `ebooklib.epub.read_epub()` (including
DRM-protected files) is caught, logged as a warning, and results in a blank view rather
than a crash.
