"""Filesystem storage for rendered audio bytes.

Audio bytes are never stored in SQLite (see app/storage/artifacts.py) --
only a relative file path is ever recorded, in a VoiceRenderManifest. This
store owns the one place that path is turned into bytes on disk.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from app.audio.errors import AudioPathError, AudioWriteError

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    """Reject a blank, absolute, or path-traversing relative path.

    Backslashes are normalized to forward slashes first so a Windows-style
    separator can't be used to smuggle an absolute path or a '..' segment
    past a POSIX-style check.
    """
    if not relative_path or not relative_path.strip():
        raise AudioPathError("relative_path cannot be blank")

    normalized = relative_path.replace("\\", "/")

    if normalized.startswith("/") or _DRIVE_LETTER_RE.match(relative_path):
        raise AudioPathError(f"relative_path must not be absolute: {relative_path!r}")

    pure_path = PurePosixPath(normalized)
    if ".." in pure_path.parts:
        raise AudioPathError(f"relative_path must not contain '..': {relative_path!r}")

    return pure_path


class AudioFileStore:
    def __init__(self, root: Path | str):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def write(self, relative_path: str, data: bytes) -> Path:
        """Atomically write data to root/relative_path and return the
        absolute path written. Writes to a temp file first and renames it
        into place, so a failure never leaves a partial file at the final
        path."""
        pure_path = _validate_relative_path(relative_path)
        final_path = self._root.joinpath(*pure_path.parts)
        temp_path = final_path.with_name(final_path.name + ".tmp")

        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(data)
            temp_path.replace(final_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise AudioWriteError(
                f"Failed to write audio file at relative path {relative_path!r}: {exc}"
            ) from exc

        return final_path
