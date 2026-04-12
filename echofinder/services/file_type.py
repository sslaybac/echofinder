import os
from pathlib import Path

from echofinder.models.file_node import FileType

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
    def resolve(self, path: Path, root: Path | None = None) -> FileType:
        if path.is_symlink():
            return self._resolve_symlink(path, root)
        if path.is_dir():
            return FileType.FOLDER
        if not path.is_file():
            return FileType.UNKNOWN

        # Primary: MIME detection
        if _MAGIC_AVAILABLE:
            try:
                mime: str = _magic.from_file(str(path), mime=True)
                result = self._mime_to_type(mime)
                if result != FileType.UNKNOWN:
                    return result
            except Exception:
                pass

        # Fallback: extension
        return self._ext_to_type(path.suffix.lower())

    def _resolve_symlink(self, path: Path, root: Path | None) -> FileType:
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

    def _mime_to_type(self, mime: str) -> FileType:
        if mime.startswith("image/"):
            return FileType.IMAGE
        if mime.startswith("video/"):
            return FileType.VIDEO
        if mime.startswith("audio/"):
            return FileType.AUDIO
        if mime == "application/pdf":
            return FileType.PDF
        # text/plain → plain text; all other text/* (html, css, x-python…) → code
        if mime == "text/plain":
            return FileType.TEXT
        if mime.startswith("text/"):
            return FileType.CODE
        # Common application/* code/data MIME types
        if mime in (
            "application/json",
            "application/javascript",
            "application/xml",
            "application/xhtml+xml",
            "application/typescript",
            "application/graphql",
        ):
            return FileType.CODE
        # application/x-* covers shell scripts, Python, Perl, Ruby, etc.
        if mime.startswith("application/x-"):
            return FileType.CODE
        return FileType.UNKNOWN

    def _ext_to_type(self, ext: str) -> FileType:
        if ext in _IMAGE_EXT:
            return FileType.IMAGE
        if ext in _VIDEO_EXT:
            return FileType.VIDEO
        if ext in _AUDIO_EXT:
            return FileType.AUDIO
        if ext == ".pdf":
            return FileType.PDF
        if ext in _CODE_EXT:
            return FileType.CODE
        if ext in _TEXT_EXT:
            return FileType.TEXT
        return FileType.UNKNOWN
