"""Filesystem storage for canonical Tí asset image files.

Tí asset bytes are never stored in SQLite (see app/storage/ti_assets.py) --
only a relative file path is ever recorded, on a TiAsset. This store owns the
one place that path is turned into (or checked against) bytes on disk.
Structurally mirrors app/visual/storage.py's VisualFileStore, plus the
read-side operations (exists/resolve) that retrieval needs and pure
render-output stores don't.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TI_ASSET_EXTENSION_BY_FORMAT, TiState
from app.ti_assets.errors import TiAssetPathError, TiAssetWriteError

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

DEFAULT_TI_ASSET_ROOT = Path("data/ti_assets")


def build_relative_path(asset_set_id: UUID, version: str, state: TiState, output_format: VisualOutputFormat) -> str:
    """The deterministic storage path convention for one canonical asset:
    {asset_set_id}/{version}/{state}.{ext} -- no other naming scheme is used
    anywhere in this subsystem."""
    ext = TI_ASSET_EXTENSION_BY_FORMAT[output_format]
    return f"{asset_set_id}/{version}/{state.value}.{ext}"


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    """Reject a blank, absolute, or path-traversing relative path.

    Backslashes are normalized to forward slashes first so a Windows-style
    separator can't be used to smuggle an absolute path or a '..' segment
    past a POSIX-style check.
    """
    if not relative_path or not relative_path.strip():
        raise TiAssetPathError("relative_path cannot be blank")

    normalized = relative_path.replace("\\", "/")

    if normalized.startswith("/") or _DRIVE_LETTER_RE.match(relative_path):
        raise TiAssetPathError(f"relative_path must not be absolute: {relative_path!r}")

    pure_path = PurePosixPath(normalized)
    if ".." in pure_path.parts:
        raise TiAssetPathError(f"relative_path must not contain '..': {relative_path!r}")

    return pure_path


class TiAssetFileStore:
    def __init__(self, root: Path | str = DEFAULT_TI_ASSET_ROOT):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative_path: str) -> Path:
        """Validate relative_path and return the absolute path under root it
        refers to. Never touches the filesystem."""
        pure_path = _validate_relative_path(relative_path)
        return self._root.joinpath(*pure_path.parts)

    def exists(self, relative_path: str) -> bool:
        return self.resolve(relative_path).is_file()

    def write(self, relative_path: str, data: bytes) -> Path:
        """Atomically write data to root/relative_path and return the
        absolute path written. Writes to a temp file first and renames it
        into place, so a failure never leaves a partial file at the final
        path."""
        final_path = self.resolve(relative_path)
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
            raise TiAssetWriteError(
                f"Failed to write Tí asset file at relative path {relative_path!r}: {exc}"
            ) from exc

        return final_path
