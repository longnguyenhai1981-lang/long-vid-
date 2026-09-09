"""Tí-asset-layer domain errors.

Two deliberately separate hierarchies, mirroring app/visual/errors.py and
app/audio/errors.py: a missing/inconsistent canonical asset is a different
kind of problem from a filesystem failure. TiAssetError and
TiAssetStorageError share no common base for this reason.
"""

from __future__ import annotations


class TiAssetError(Exception):
    """Base class for all Tí-canonical-asset domain errors."""


class TiAssetStateNotFoundError(TiAssetError):
    """Raised when a requested TiState has no matching TiAsset in the given
    list. TiAssetSet's own construction-time completeness check makes this
    unreachable for a validly-stored, active set -- this exists as an
    explicit, no-silent-fallback safety net for that invariant, not as a
    routine code path."""


class TiAssetMissingFileError(TiAssetError):
    """Raised when a TiAsset's recorded relative_path has no corresponding
    file on disk -- metadata/filesystem drift. Never silently substitutes a
    placeholder or falls back to AI-generated mascot imagery."""


class TiAssetInvalidImageError(TiAssetError):
    """Raised by app/ti_assets/png_metadata.py when a file is not a valid
    PNG, is truncated/malformed, or otherwise cannot be inspected for the
    metadata ingestion needs (Phase 21.1)."""


class TiAssetIngestionError(TiAssetError):
    """Raised by app/ti_assets/ingest.py when a source directory fails
    validation (missing/invalid/non-transparent files) or a version
    conflicts with an already-stored TiAssetSet (Phase 21.1). Raised
    before any database or file-store mutation happens."""


class TiAssetStorageError(Exception):
    """Base class for all Tí-asset filesystem-layer errors. Deliberately NOT
    a TiAssetError subclass -- a storage failure is never a domain-validation
    failure."""


class TiAssetPathError(TiAssetStorageError):
    """Raised when a requested relative path is blank, absolute, or escapes
    the Tí-asset-store root (e.g. via '..')."""


class TiAssetWriteError(TiAssetStorageError):
    """Raised when writing a Tí asset file to disk fails."""
