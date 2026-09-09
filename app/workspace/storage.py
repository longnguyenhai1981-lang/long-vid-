"""Content-addressed blob storage for workspace assets (Phase 36
requirements #10-11, #35-38).

Mirrors app/audio/storage.py's/app/ti_assets/storage.py's own
`_validate_relative_path` + atomic-temp-then-rename write pattern
exactly. The one addition here is that the relative path is ALWAYS
derived from (project_id, sha256, extension) -- never from the
original, untrusted filename -- so:

- stored paths are stable and immutable (requirement #10/#11)
- two ingests of the same bytes for the same project resolve to the
  SAME path, making a second identical ingest a cheap no-op existence
  check rather than a duplicate write (requirement #9/#38 concurrency
  tolerance -- no distributed lock needed, `write_if_absent` is
  naturally idempotent)
- an unsafe original filename (spaces, Vietnamese Unicode, parentheses)
  never touches the filesystem path at all; it is preserved only as
  WorkspaceAsset.original_filename metadata (requirement #11)
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.workspace.errors import WorkspaceBlobWriteError, WorkspacePathError

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

DEFAULT_WORKSPACE_ROOT = Path("data/projects")


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    if not relative_path or not relative_path.strip():
        raise WorkspacePathError("relative_path cannot be blank")

    normalized = relative_path.replace("\\", "/")

    if normalized.startswith("/") or _DRIVE_LETTER_RE.match(relative_path):
        raise WorkspacePathError(f"relative_path must not be absolute: {relative_path!r}")

    pure_path = PurePosixPath(normalized)
    if ".." in pure_path.parts:
        raise WorkspacePathError(f"relative_path must not contain '..': {relative_path!r}")

    return pure_path


def blob_relative_path(project_id: UUID, sha256: str, extension: str) -> str:
    """The one deterministic content-addressed path convention:
    {project_id}/workspace/blobs/{sha256[:2]}/{sha256}.{ext} -- the two
    leading hex chars avoid one directory holding an unbounded number of
    files as a project's asset count grows."""
    ext = extension.lstrip(".")
    return f"{project_id}/workspace/blobs/{sha256[:2]}/{sha256}.{ext}"


class WorkspaceBlobStore:
    def __init__(self, root: Path | str = DEFAULT_WORKSPACE_ROOT):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative_path: str) -> Path:
        """Validate relative_path and return the absolute path under
        root it refers to. Never touches the filesystem -- this is the
        one path-safety choke point every read/write goes through
        (requirement #35: no write/read may escape the managed root)."""
        pure_path = _validate_relative_path(relative_path)
        return self._root.joinpath(*pure_path.parts)

    def exists(self, relative_path: str) -> bool:
        return self.resolve(relative_path).is_file()

    def write_if_absent(self, relative_path: str, data: bytes) -> Path:
        """Atomically write data to root/relative_path, unless a file is
        already there (content-addressed identity means "already there"
        can only mean "identical bytes", so this is a safe, lock-free
        no-op on a repeat ingest -- requirement #38). Writes to a temp
        sibling file first and renames it into place, so a failure never
        leaves a partial blob at the final path (requirement #36)."""
        final_path = self.resolve(relative_path)
        if final_path.is_file():
            return final_path

        temp_path = final_path.with_name(final_path.name + f".tmp-{id(data)}")
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(data)
            temp_path.replace(final_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkspaceBlobWriteError(
                f"Failed to write workspace blob at relative path {relative_path!r}: {exc}"
            ) from exc

        return final_path

    def read(self, relative_path: str) -> bytes:
        return self.resolve(relative_path).read_bytes()
