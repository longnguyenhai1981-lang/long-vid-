"""Domain errors for app/workspace/ (Phase 36 requirement #34).

Narrow, specific errors -- never a generic "ingestion failed" catch-all --
so a caller (the CLI, ProductionService) can react to a specific cause
rather than string-matching a message.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base class for all app/workspace/ errors."""


class UnsupportedWorkspaceFileTypeError(WorkspaceError):
    """Raised when a file's extension is not one of the deterministically
    supported categories (document/image/audio/video)."""


class WorkspaceFileMissingError(WorkspaceError):
    """Raised when the given source_path does not exist or is not a file."""


class WorkspaceFileEmptyError(WorkspaceError):
    """Raised when the given source_path is zero bytes."""


class WorkspaceImageCorruptError(WorkspaceError):
    """Raised when Pillow cannot decode an image file claiming an image
    extension."""


class WorkspaceMediaUnreadableError(WorkspaceError):
    """Raised when a PDF's signature is missing, or (when ffprobe is on
    PATH) ffprobe cannot read an audio/video file."""


class AmbiguousProjectBriefError(WorkspaceError):
    """Raised when brief resolution finds more than one PROJECT_BRIEF
    asset for a project with no active selection and no explicit id given
    -- never a silent 'latest wins' guess."""


class InvalidWorkspaceAssetTypeError(WorkspaceError):
    """Raised when an explicitly-referenced asset does not have the
    WorkspaceAssetType the caller required (e.g. --brief-asset pointing
    at something that is not a PROJECT_BRIEF)."""


class WorkspaceBlobWriteError(WorkspaceError):
    """Raised when the atomic blob write itself fails (disk error, path
    permission, etc.) -- distinct from a validation failure, since the
    input file was fine but storage could not be written."""


class WorkspacePathError(WorkspaceError):
    """Raised when a relative path would escape the managed workspace
    root (requirement #35) -- mirrors app/audio/storage.py's own
    AudioPathError/app/ti_assets/storage.py's own TiAssetPathError."""
