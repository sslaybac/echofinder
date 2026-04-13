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
Ten-stage plan. **Stages 1–8 are complete.** Stages 9–10 remain.

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
| 9     | Audio Playback               | Remaining |
| 10    | Video Playback               | Remaining |
| 11    | EPUB Preview                 | Remaining |

Stage 11 was added after the Staging Plan v2 was written; the staging plan does not
include it. The Stage 11 spec is in `planning/Echofinder_Stage11_Context.md` (local
only — not in the repository); see the Stage 11 section below for implementation
context.

Stages 9 and 10 use python-vlc. Stage 9 installs the dependency; Stage 10 reuses it.
VLC must be installed on the host system. Embedding VLC video output in a Qt widget
requires platform-specific wiring (`win_id` / `x_window` handle) — test on both Alma
Linux 9 and Windows 11.

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

## Stage 11: EPUB Preview
The full spec is in `planning/Echofinder_Stage11_Context.md` (local only — not in the
repository). The section below is self-contained for implementation purposes.

**Dependencies** — install at the start of Stage 11:
```
uv add ebooklib
uv add PyQt6-WebEngine
```
On some Linux distributions an additional system package may be required for
WebEngine (e.g. `qt6-webengine`). Confirm availability on Alma Linux 9 first.

**Architecture** — `ebooklib` parses the EPUB (spine, chapter HTML, embedded assets);
`QWebEngineView` renders chapters; a `QWebEngineUrlSchemeHandler` using the custom
scheme `echofinder-epub://` serves embedded assets (images, CSS, fonts) from the
in-memory book to the web view. Do not write extracted assets to disk.

`QWebEngineUrlScheme.registerScheme()` must be called before `QApplication()` is
instantiated.

**Optional dependency handling** — `PyQt6-WebEngine` is not always present (~200 MB).
Guard the import in `epub_widget.py` with a `_WEBENGINE_AVAILABLE` flag. If absent,
EPUB files fall back to the unsupported widget; the application must not crash.

**File type resolver** — insert EPUB between PDF and unsupported in the precedence
order. MIME type: `application/epub+zip`. Extension: `.epub`. Add `FileType.EPUB` to
the enum in `models/file_node.py`.

**Settled decisions (do not re-propose rejected alternatives)**
- Rendering: `QWebEngineView` only. Stripping HTML for `TextPreviewWidget` was
  rejected (discards all formatting).
- Asset serving: URL scheme handler only. Writing to a temp directory was rejected
  (unnecessary I/O, lifecycle complexity).
- Scope: EPUB only. MOBI/AZW and other ebook formats are out of scope.
- Session state: chapter index, scroll position (per chapter), and zoom level are
  retained in memory for the session; not persisted to disk.

**Open questions (do not block implementation)**
- EPUB 3 JavaScript in chapter HTML: load as-is; only strip `<script>` tags if
  navigation conflicts are observed in practice.
- DRM-protected EPUBs: `ebooklib` will raise an exception; catch it and show the
  unreadable file widget.
