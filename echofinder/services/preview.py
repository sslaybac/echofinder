"""Preview data loading service.

All filesystem I/O for the preview pane lives here.  Individual widgets receive
pre-loaded data only; they never open files themselves.
"""
from __future__ import annotations

from pathlib import Path


def read_text_for_preview(path: Path) -> tuple[str, str]:
    """Read *path* and return ``(decoded_text, encoding_name)``.

    Encoding cascade (Stage 4 spec §5):
    1. UTF-8 — pure ASCII files are valid UTF-8 and succeed here.
    2. charset-normalizer — detect encoding, retry decode.
    3. Latin-1 fallback — maps every possible byte value; always succeeds.

    The returned *encoding_name* must be surfaced in the metadata panel (Stage 5).

    Raises:
        OSError: if the file cannot be opened or read at all.
    """
    raw: bytes = path.read_bytes()

    # Step 1: UTF-8
    try:
        return raw.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        pass

    # Step 2: charset-normalizer
    # Skip UTF-16/UTF-32 suggestions: if the file failed UTF-8, a multi-byte
    # Unicode encoding is almost certainly a false positive (especially for short
    # content where the detector has little to work from).  Those codecs are
    # handled by the UTF-8 step via their BOM markers.
    try:
        from charset_normalizer import from_bytes  # type: ignore[import-untyped]

        result = from_bytes(raw).best()
        if result is not None and result.encoding:
            enc_name = result.encoding.lower().replace("-", "_")
            is_multibyte_unicode = any(
                mb in enc_name for mb in ("utf_16", "utf_32", "utf16", "utf32")
            )
            if not is_multibyte_unicode:
                try:
                    decoded = raw.decode(result.encoding)
                    return decoded, result.encoding.upper()
                except (UnicodeDecodeError, LookupError):
                    pass
    except ImportError:
        pass

    # Step 3: Latin-1 — always produces a displayable result
    return raw.decode("latin-1"), "Latin-1"


def load_image_bytes(path: Path) -> bytes:
    """Read and return the raw bytes of an image file.

    Raises:
        OSError: if the file cannot be read.
    """
    return path.read_bytes()
