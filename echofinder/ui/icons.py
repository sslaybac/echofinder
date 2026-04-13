from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from echofinder.models.file_node import FileType, HashState, OwnershipState, PermissionState

_ICONS_DIR = Path(__file__).parent.parent / "resources" / "icons"
_ICON_SIZE = 16
_ICON_COLOR = "#3d3d3d"
_ICON_COLOR_MUTED = "#aaaaaa"  # for transient/non-alarming states (hourglass)

# Maps FileType → Phosphor icon filename stem
_TYPE_TO_ICON: dict[FileType, str] = {
    FileType.FOLDER: "folder",
    FileType.IMAGE: "image",
    FileType.VIDEO: "monitor-play",
    FileType.AUDIO: "speaker-simple-high",
    FileType.PDF: "file-pdf",
    FileType.TEXT: "file-text",
    FileType.CODE: "file-code",
    FileType.SYMLINK_INTERNAL: "link-simple",
    FileType.SYMLINK_EXTERNAL: "link-simple-break",
    FileType.UNKNOWN: "question",
}

# Slot 2: ownership state → icon name
_OWNERSHIP_TO_ICON: dict[OwnershipState, str] = {
    OwnershipState.INDIVIDUAL: "user",
    OwnershipState.GROUP: "users",
    # NEITHER uses a composited icon (see _build_neither_ownership_icon)
}

# Slot 3: permission state → icon name (None = no icon shown)
_PERMISSION_TO_ICON: dict[PermissionState, str | None] = {
    PermissionState.READ_WRITE: "pencil",
    PermissionState.READ_ONLY: "eye",
    PermissionState.NOT_READABLE: "lock",
}

# Slot 4: hash state → icon name (None = no icon shown for UNIQUE)
_HASH_TO_ICON: dict[HashState, str | None] = {
    HashState.NOT_HASHED: "hourglass",     # muted color (background work)
    HashState.UNIQUE: None,                # no slot-4 icon
    HashState.DUPLICATE_GENERAL: "copy",
    HashState.DUPLICATE_SPECIFIC: "star-half",  # chosen over composited double-star
    HashState.RENAME_CONFLICT: "warning",
}

_cache: dict[str, QIcon] = {}


# ---------------------------------------------------------------------------
# Public API — slot icon accessors
# ---------------------------------------------------------------------------

def icon_for_type(file_type: FileType) -> QIcon:
    """Slot 1: file-type icon."""
    name = _TYPE_TO_ICON.get(file_type, "question")
    return _load(name)


def icon_for_ownership(state: OwnershipState) -> QIcon | None:
    """Slot 2: ownership icon.  Returns None for INDIVIDUAL/GROUP if load fails.
    The NEITHER state uses a composited icon built at startup."""
    if state == OwnershipState.NEITHER:
        return _neither_ownership_icon()
    name = _OWNERSHIP_TO_ICON.get(state)
    if name is None:
        return None
    icon = _load(name)
    return icon if not icon.isNull() else None


def icon_for_permission(state: PermissionState) -> QIcon | None:
    """Slot 3: permission icon.  Returns None only if the load fails."""
    name = _PERMISSION_TO_ICON.get(state)
    if name is None:
        return None
    icon = _load(name)
    return icon if not icon.isNull() else None


def icon_for_hash_state(state: HashState) -> QIcon | None:
    """Slot 4: hashing/duplicate icon.  Returns None for UNIQUE (no icon shown)."""
    name = _HASH_TO_ICON.get(state)
    if name is None:
        return None
    if state == HashState.NOT_HASHED:
        # Hourglass is muted — it represents transient background work, not an error.
        return _load_muted("hourglass")
    icon = _load(name)
    return icon if not icon.isNull() else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load(name: str, *, color: str = _ICON_COLOR) -> QIcon:
    """Load a Phosphor SVG icon by name, caching the result.

    Args:
        name: Filename stem of the SVG under ``resources/icons/``,
            e.g. ``'folder'`` for ``folder.svg``.
        color: CSS hex color string substituted for ``currentColor`` in the
            SVG source.  Defaults to the standard dark-grey icon color.

    Returns:
        A ``QIcon`` backed by the rendered pixmap.  Returns a null ``QIcon``
        if the file does not exist or rendering fails.
    """
    cache_key = f"{name}:{color}"
    if cache_key in _cache:
        return _cache[cache_key]

    svg_path = _ICONS_DIR / f"{name}.svg"
    if not svg_path.exists():
        icon = QIcon()
    else:
        try:
            icon = _render(svg_path, color=color)
        except Exception:
            icon = QIcon()

    _cache[cache_key] = icon
    return icon


def _load_muted(name: str) -> QIcon:
    """Load an icon with the muted colour, used for non-alarming transient states."""
    return _load(name, color=_ICON_COLOR_MUTED)


def _render(
    path: Path,
    size: int = _ICON_SIZE,
    color: str = _ICON_COLOR,
) -> QIcon:
    """Render an SVG file to a ``QIcon`` at *size* × *size* pixels.

    Substitutes *color* for ``currentColor`` in the SVG source so that all
    Phosphor icon strokes adopt the application's icon color scheme.

    Args:
        path: Absolute ``Path`` to the ``.svg`` file.
        size: Pixel size of the square output pixmap.
        color: CSS hex color string for ``currentColor`` substitution.

    Returns:
        A ``QIcon`` wrapping the rendered pixmap.
    """
    svg_text = path.read_text(encoding="utf-8")
    # Phosphor SVGs use currentColor; substitute our desired color
    svg_text = svg_text.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg_text.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def _render_pixmap(
    name: str,
    size: int = _ICON_SIZE,
    color: str = _ICON_COLOR,
) -> QPixmap | None:
    """Render a named SVG to a QPixmap (helper for compositing)."""
    svg_path = _ICONS_DIR / f"{name}.svg"
    if not svg_path.exists():
        return None
    try:
        svg_text = svg_path.read_text(encoding="utf-8")
        svg_text = svg_text.replace("currentColor", color)
        renderer = QSvgRenderer(QByteArray(svg_text.encode()))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return pixmap
    except Exception:
        return None


def _neither_ownership_icon() -> QIcon:
    """Build and cache the 'neither' ownership icon.

    Composites a small question-mark badge over the bottom-right corner of the
    desktop-tower icon.  Constructed once at first use and cached thereafter.

    At 16 px the Phosphor 'question' glyph is too fine to read as a badge, so
    we draw a plain "?" string via QPainter.drawText() instead.
    """
    cache_key = "_neither_ownership"
    if cache_key in _cache:
        return _cache[cache_key]

    base = _render_pixmap("desktop-tower", size=_ICON_SIZE, color=_ICON_COLOR)
    if base is None:
        icon = QIcon()
        _cache[cache_key] = icon
        return icon

    canvas = QPixmap(_ICON_SIZE, _ICON_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, base)

    # Badge: white filled circle + "?" text in the bottom-right quadrant
    badge_size = 7
    badge_x = _ICON_SIZE - badge_size
    badge_y = _ICON_SIZE - badge_size

    from PyQt6.QtGui import QBrush, QColor, QFont, QPen

    # White backing circle so the "?" is readable over any background
    painter.setPen(QPen(QColor("#ffffff"), 0))
    painter.setBrush(QBrush(QColor("#ffffff")))
    painter.drawEllipse(badge_x, badge_y, badge_size, badge_size)

    # "?" text in the badge colour
    font = QFont()
    font.setPixelSize(badge_size - 1)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QPen(QColor(_ICON_COLOR)))
    from PyQt6.QtCore import QRect
    painter.drawText(
        QRect(badge_x, badge_y, badge_size, badge_size),
        Qt.AlignmentFlag.AlignCenter,
        "?",
    )

    painter.end()

    icon = QIcon(canvas)
    _cache[cache_key] = icon
    return icon


def _composited_double_star_icon() -> QIcon:
    """Option B: two 'star' pixmaps composited with an offset.

    Implemented for empirical comparison against star-half (Option A) at actual
    rendering size.  The back star is drawn at reduced opacity; the front star
    at full opacity.  At 16 px an offset of 2 px is used.

    Evaluation: at 16 px the 2-px offset produces a slight shadow/blur effect
    rather than a clearly readable 'stack of two stars'.  The shape reads as a
    single slightly smeared star, making it difficult to distinguish from the
    plain star-half glyph and providing no benefit over Option A.

    Decision: star-half (Option A) is used.  See decisions log.
    """
    cache_key = "_double_star"
    if cache_key in _cache:
        return _cache[cache_key]

    offset = 2
    star_size = _ICON_SIZE - offset

    star_px = _render_pixmap("star", size=star_size, color=_ICON_COLOR)
    if star_px is None:
        icon = QIcon()
        _cache[cache_key] = icon
        return icon

    canvas = QPixmap(_ICON_SIZE, _ICON_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)

    painter.setOpacity(0.5)
    painter.drawPixmap(offset, offset, star_px)  # back star (dimmed)

    painter.setOpacity(1.0)
    painter.drawPixmap(0, 0, star_px)            # front star (full opacity)

    painter.end()

    icon = QIcon(canvas)
    _cache[cache_key] = icon
    return icon
