"""Storage-layer domain errors."""

from __future__ import annotations


class ProjectNotFoundError(Exception):
    """Raised when a project_id has no matching row."""


class ArtifactNotFoundError(Exception):
    """Raised when no artifact exists for the requested (project_id, artifact_type)."""


class ArtifactValidationError(Exception):
    """Raised when a stored artifact payload is corrupt or does not match the requested model."""


class ModuleRunNotFoundError(Exception):
    """Raised when a run_id has no matching row."""


class ProductionRunNotFoundError(Exception):
    """Raised when a ProductionRun id has no matching row (Phase 33)."""


class TiAssetSetNotFoundError(Exception):
    """Raised when a requested (asset_set_id, version) has no matching row,
    or no row is currently marked active."""


class TiAssetSetValidationError(Exception):
    """Raised when a stored TiAssetSet payload is corrupt or does not
    validate as TiAssetSet."""


class MultipleActiveTiAssetSetsError(Exception):
    """Raised if more than one TiAssetSetRow is ever found with
    is_active=True -- a defensive invariant check; app/storage/ti_assets.py's
    save/activate functions are written to make this unreachable in normal
    use."""


class WorkspaceAssetNotFoundError(Exception):
    """Raised when a WorkspaceAsset id has no matching row (Phase 36)."""
