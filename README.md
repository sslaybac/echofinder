# Echofinder

[![Tests](https://github.com/sslaybac/echofinder/actions/workflows/test.yml/badge.svg)](https://github.com/sslaybac/echofinder/actions/workflows/test.yml)

Echofinder is a cross-platform desktop application for exploring and managing your
local file system, available on Windows and Linux. It combines a folder-tree interface
with rich file previewing — displaying images, playing audio and video, rendering
PDFs, and showing code with syntax highlighting — all without leaving the app or
needing an internet connection.

A key feature is automatic duplicate detection: the app silently scans your files in the
background and flags duplicates directly within the file tree. From there, you can
inspect, navigate to, and clean up duplicate files without ever opening a separate tool.
File management basics — moving and deleting — are handled in-app and reflected
instantly.

---

## Features

- **File tree with keyboard navigation** — Browse your file system in a resizable tree
  panel. Use arrow keys to navigate, Enter to expand/collapse folders, and F2 to rename.
  The tree updates automatically when files change on disk.

- **Background duplicate detection** — When you open a folder, Echofinder silently
  hashes every file in the background. Duplicate files are flagged directly in the tree
  with a visual indicator. Selecting a file highlights all its duplicates at once.

- **File preview** — Selecting a file shows its contents in the preview pane without
  opening an external application:
  - Images are rendered inline.
  - Text and code files are displayed with syntax highlighting.
  - Folder contents are shown as a navigable list.
  - Symlinks show their target path with a navigation link.
  - PDF files are rendered inline with multi-page scroll and zoom support.
  - EPUB files are rendered inline with chapter navigation and zoom support.
  - Audio files are played back inline with play/pause, stop, seek, and
    volume controls. VLC must be installed on the host system.
  - Video files are played back inline with the same controls as audio.

- **Metadata panel** — Displays the SHA-256 hash, MIME type, programming language,
  character encoding, and duplicate count for the selected file. The duplicates row
  is a clickable menu that jumps directly to each matching file.

- **File operations** — Delete files (to the system trash), rename them inline, and
  move them via three methods: a folder picker dialog, keyboard movement mode (M or F6),
  or drag-and-drop. Cross-filesystem moves and directory merges are handled with
  appropriate confirmations. All operations are reflected in the tree immediately.

- **Live filesystem polling** — Echofinder polls the current root directory every
  30 seconds to detect external changes. Files added, removed, or modified by other
  applications appear (or disappear) in the tree automatically without requiring the
  user to refresh.

---

## Technology Stack

| Concern                  | Library / Tool             | Notes                                      |
|--------------------------|----------------------------|--------------------------------------------|
| GUI framework            | PyQt6                      | Widgets, signals/slots, threading          |
| File hashing             | hashlib (stdlib)           | SHA-256                                    |
| Hash / metadata cache    | sqlite3 (stdlib)           | Persistent across sessions                 |
| Filesystem polling       | os (stdlib)                | os.stat, os.walk                           |
| Trash / deletion         | send2trash                 | Cross-platform recycle bin support         |
| Image preview            | Pillow                     | JPEG, PNG, GIF, TIFF, BMP, WebP            |
| File type detection      | python-magic               | MIME type via libmagic                     |
| PDF rendering            | PyMuPDF (fitz)             | Multi-page preview with zoom               |
| EPUB rendering           | ebooklib + PyQt6-WebEngine | Chapter navigation, zoom, asset serving    |
| Audio playback           | python-vlc                 | Wraps libvlc; VLC must be installed        |
| Video playback           | PyQt6.QtMultimedia         | FFmpeg backend; no additional install      |
| Syntax highlighting      | Pygments                   | 500+ languages                             |
| Encoding detection       | charset-normalizer         | Automatic detection for non-UTF-8 text     |
| Config file paths        | platformdirs               | OS-appropriate locations                   |
| Package management       | uv                         |                                            |

---

## Prerequisites

- **Python 3.11 or later**
- **uv** — install from [https://docs.astral.sh/uv/](https://docs.astral.sh/uv/getting-started/installation/)

### System libraries (Linux only)

On Linux, `python-magic` requires **libmagic** and audio playback requires **VLC**:

```bash
# Alma Linux / RHEL / Fedora
sudo dnf install file-libs vlc

# Debian / Ubuntu
sudo apt install libmagic1 vlc
```

On Windows, `python-magic` bundles the necessary DLL automatically. VLC must be
installed separately from [videolan.org](https://www.videolan.org/).

---

## Installation and Running

### Clone and install

```bash
git clone https://github.com/sslaybac/echofinder.git
cd echofinder
uv sync
```

This installs all Python dependencies into an isolated virtual environment managed by
`uv`.

### Run the application

```bash
uv run python -m echofinder
```

The application window opens. Click **Open Folder...** in the toolbar (or press the
button in the empty state view) to choose a root directory and begin exploring.

---

## Target Platforms

| Platform        | Status                      |
|-----------------|-----------------------------|
| Alma Linux 9    | Supported, tested in CI     |
| Windows 11      | Supported, manually tested  |

> **Note:** CI runs on Ubuntu. Windows support is maintained manually; no automated
> Windows tests are currently configured. VLC and libmagic require separate system
> installs on Windows — see [Prerequisites](#prerequisites).
