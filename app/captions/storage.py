"""Filesystem storage for exported subtitle files (Phase 31).

SRT bytes are never stored in SQLite (see app/storage/artifacts.py) --
only a relative file path is ever recorded, in a SubtitleFileAsset. This
store owns the one place that path is turned into bytes on disk --
mirrors app/audio/storage.py's AudioFileStore exactly (same path-
traversal/absolute-path guard, same atomic temp-write-then-rename).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from app.captions.errors import CaptionError

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


class SubtitlePathError(CaptionError):
    """Raised for a blank, absolute, or path-traversing relative path
    passed to SubtitleFileStore.write."""


class SubtitleWriteError(CaptionError):
    """Raised when writing a subtitle file to disk fails."""


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    """Reject a blank, absolute, or path-traversing relative path.

    Backslashes are normalized to forward slashes first so a Windows-style
    separator can't be used to smuggle an absolute path or a '..' segment
    past a POSIX-style check.
    """
    if not relative_path or not relative_path.strip():
        raise SubtitlePathError("relative_path cannot be blank")

    normalized = relative_path.replace("\\", "/")

    if normalized.startswith("/") or _DRIVE_LETTER_RE.match(relative_path):
        raise SubtitlePathError(f"relative_path must not be absolute: {relative_path!r}")

    pure_path = PurePosixPath(normalized)
    if ".." in pure_path.parts:
        raise SubtitlePathError(f"relative_path must not contain '..': {relative_path!r}")

    return pure_path


class SubtitleFileStore:
    def __init__(self, root: Path | str):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def write(self, relative_path: str, text: str, *, encoding: str = "utf-8") -> Path:
        """Atomically write `text` to root/relative_path and return the
        absolute path written. Writes to a temp file first and renames it
        into place, so a failure never leaves a partial file at the final
        path."""
        pure_path = _validate_relative_path(relative_path)
        final_path = self._root.joinpath(*pure_path.parts)
        temp_path = final_path.with_name(final_path.name + ".tmp")

        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(text, encoding=encoding, newline="")
            temp_path.replace(final_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SubtitleWriteError(
                f"Failed to write subtitle file at relative path {relative_path!r}: {exc}"
            ) from exc

        return final_path
