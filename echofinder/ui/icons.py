from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from echofinder.models.file_node import FileType

_ICONS_DIR = Path(__file__).parent.parent / "resources" / "icons"
_ICON_SIZE = 16
_ICON_COLOR = "#3d3d3d"

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

_cache: dict[str, QIcon] = {}


def icon_for_type(file_type: FileType) -> QIcon:
    name = _TYPE_TO_ICON.get(file_type, "question")
    return _load(name)


def _load(name: str) -> QIcon:
    if name in _cache:
        return _cache[name]

    svg_path = _ICONS_DIR / f"{name}.svg"
    if not svg_path.exists():
        icon = QIcon()
    else:
        try:
            icon = _render(svg_path)
        except Exception:
            icon = QIcon()

    _cache[name] = icon
    return icon


def _render(path: Path, size: int = _ICON_SIZE, color: str = _ICON_COLOR) -> QIcon:
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
