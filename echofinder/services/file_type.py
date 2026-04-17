import logging
import os
from pathlib import Path

from echofinder.models.file_node import FileType

logger = logging.getLogger(__name__)

try:
    import magic as _magic

    _MAGIC_AVAILABLE = True
except ImportError:
    _MAGIC_AVAILABLE = False

# Extension sets for fallback detection
_IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".ico", ".heic", ".heif", ".avif", ".svg",
}
_VIDEO_EXT = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpeg", ".mpg",
}
_AUDIO_EXT = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus",
    ".aiff", ".alac",
}
_CODE_EXT = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx",
    ".java", ".kt", ".kts", ".scala", ".groovy",
    ".cs", ".vb", ".fs",
    ".go", ".rs", ".rb", ".php", ".swift",
    ".r", ".lua", ".perl", ".pl",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".yaml", ".yml", ".toml", ".json", ".jsonc",
    ".xml", ".html", ".htm", ".xhtml",
    ".css", ".scss", ".sass", ".less",
    ".sql", ".graphql", ".proto",
    ".tf", ".hcl", ".dockerfile", ".makefile",
    ".zig", ".nim", ".ex", ".exs", ".erl", ".hrl",
    ".clj", ".cljs", ".lisp", ".scm", ".hs", ".elm",
}
_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".ini", ".cfg", ".conf", ".env", ".properties",
}


class FileTypeResolver:
    """Classifies filesystem entries as ``FileType`` values.

    Detection uses two strategies in priority order:

    1. **MIME type** via ``python-magic`` (libmagic) — reliable for binary
       formats such as images, video, audio, and PDF.
    2. **File extension** fallback — used when libmagic is unavailable or
       returns an ambiguous ``text/*`` or ``application/*`` MIME type.

    A single instance is shared across the application lifetime; it is
    stateless and safe to call from any thread.
    """

    def resolve_fast(self, path: Path, root: Path | None = None) -> FileType:
        """Classify *path* using only stat calls and file extension — no magic read.

        Used by ``scan_directory`` so that directory expansion never blocks the
        main thread on disk I/O.  The returned type may be ``UNKNOWN`` for files
        whose extension is unrecognised; the hashing engine will emit the
        MIME-detected type later via ``file_hashed``, and the tree model will
        promote the node at that point.
        """
        if path.is_symlink():
            result = self._resolve_symlink(path, root)
            logger.debug("Resolved (fast) %s → %s (symlink)", path, result)
            return result
        if path.is_dir():
            return FileType.FOLDER
        if not path.is_file():
            return FileType.UNKNOWN
        result = self._ext_to_type(path.suffix.lower())
        logger.debug("Resolved (fast) %s → %s (extension only)", path, result)
        return result

    def resolve(self, path: Path, root: Path | None = None) -> FileType:
        """Classify *path* as a ``FileType``.

        Symlinks are classified first (before any ``is_dir`` / ``is_file``
        checks) so that the symlink type is not obscured by the target type.

        Args:
            path: The filesystem path to classify.
            root: The current tree root; required for
                ``SYMLINK_INTERNAL`` vs ``SYMLINK_EXTERNAL`` discrimination.

        Returns:
            The ``FileType`` value for *path*.
        """
        if path.is_symlink():
            result = self._resolve_symlink(path, root)
            logger.debug("Resolved %s: mime=None → %s (symlink)", path, result)
            return result
        if path.is_dir():
            logger.debug("Resolved %s: mime=None → %s (directory)", path, FileType.FOLDER)
            return FileType.FOLDER
        if not path.is_file():
            logger.debug("Resolved %s: mime=None → %s (not a file)", path, FileType.UNKNOWN)
            return FileType.UNKNOWN

        # Primary: MIME detection
        mime: str | None = None
        if _MAGIC_AVAILABLE:
            try:
                mime = _magic.from_file(str(path), mime=True)
                result = self._mime_to_type(mime)
                if result != FileType.UNKNOWN:
                    logger.debug("Resolved %s: mime=%s → %s", path, mime, result)
                    return result
            except Exception as exc:
                logger.warning("python-magic raised exception for %s: %s", path, exc)
                mime = None

        # Fallback: extension
        result = self._ext_to_type(path.suffix.lower())
        logger.debug("Resolved %s: mime=%s → %s (extension fallback)", path, mime, result)
        return result

    def _resolve_symlink(self, path: Path, root: Path | None) -> FileType:
        """Determine whether a symlink target is inside or outside *root*.

        Args:
            path: The symlink path.
            root: The current tree root used for internal/external comparison.

        Returns:
            ``SYMLINK_INTERNAL`` if the resolved target is under *root*,
            otherwise ``SYMLINK_EXTERNAL``.
        """
        try:
            raw_target = os.readlink(str(path))
            target = Path(raw_target)
            if not target.is_absolute():
                target = path.parent / target
            target = target.resolve()
            if root is not None and target.is_relative_to(root.resolve()):
                return FileType.SYMLINK_INTERNAL
        except OSError:
            pass
        return FileType.SYMLINK_EXTERNAL

    @staticmethod
    def mime_to_file_type(mime: str) -> FileType:
        """Map a MIME type string to a ``FileType``.

        Text and code subtypes are not distinguished here; the caller
        falls back to ``_ext_to_type`` for those cases.

        Args:
            mime: A MIME type string, e.g. ``'image/png'``.

        Returns:
            The matching ``FileType``, or ``UNKNOWN`` if not recognised.
        """
        if mime.startswith("image/"):
            return FileType.IMAGE
        if mime.startswith("video/"):
            return FileType.VIDEO
        if mime.startswith("audio/"):
            return FileType.AUDIO
        if mime == "application/pdf":
            return FileType.PDF
        if mime == "application/epub+zip":
            return FileType.EPUB
        # text/* and application/* code types — defer to extension for text/code split
        return FileType.UNKNOWN

    # Keep the private alias so existing internal call sites are unaffected.
    _mime_to_type = mime_to_file_type

    def _ext_to_type(self, ext: str) -> FileType:
        """Map a lowercase file extension (including the dot) to a ``FileType``.

        Args:
            ext: Lowercase extension string, e.g. ``'.py'``.

        Returns:
            The matching ``FileType``, or ``UNKNOWN`` if not in any known set.
        """
        if ext in _IMAGE_EXT:
            return FileType.IMAGE
        if ext in _VIDEO_EXT:
            return FileType.VIDEO
        if ext in _AUDIO_EXT:
            return FileType.AUDIO
        if ext == ".pdf":
            return FileType.PDF
        if ext == ".epub":
            return FileType.EPUB
        if ext in _CODE_EXT:
            return FileType.CODE
        if ext in _TEXT_EXT:
            return FileType.TEXT
        return FileType.UNKNOWN
