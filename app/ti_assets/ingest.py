"""Deterministic ingestion of a human-supplied canonical Tí asset directory
into the Phase 21 storage/retrieval system (Phase 21.1).

MVP policy, enforced exactly: PNG only, transparent background (alpha
channel or tRNS-based transparency) required, all 8 TiState files required
under their exact filename (STATE.png), no state inference from any other
filename, no AI generation of any kind, no automatic repair of a bad or
missing file, no silent fallback if a file is missing or invalid. The
entire source directory is validated before any database or file-store
mutation -- a validation failure (including a version already in use)
leaves both completely untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TI_ASSET_MIME_BY_FORMAT, TiAsset, TiAssetSet, TiAssetType, TiState
from app.storage.ti_assets import list_ti_asset_sets, save_ti_asset_set
from app.ti_assets.errors import TiAssetIngestionError, TiAssetInvalidImageError
from app.ti_assets.png_metadata import PngMetadata, read_png_metadata
from app.ti_assets.storage import TiAssetFileStore, build_relative_path

REQUIRED_FILENAMES: dict[TiState, str] = {state: f"{state.value}.png" for state in TiState}
"""The exact, closed filename set ingestion accepts -- no fuzzy matching,
no alternate casing, no inference of a state from any other name."""


def _validate_source_directory(source_dir: Path) -> dict[TiState, tuple[bytes, PngMetadata]]:
    """Read and validate every required file, collecting every problem
    found rather than stopping at the first one, and raise
    TiAssetIngestionError naming all of them together if any exist.
    Returns validated (bytes, PngMetadata) per state only on full success --
    no database or file-store mutation has happened yet at this point."""
    if not source_dir.is_dir():
        raise TiAssetIngestionError(f"source directory does not exist: {source_dir}")

    problems: list[str] = []
    validated: dict[TiState, tuple[bytes, PngMetadata]] = {}

    for state, filename in REQUIRED_FILENAMES.items():
        file_path = source_dir / filename
        if not file_path.is_file():
            problems.append(f"missing required file: {filename}")
            continue

        data = file_path.read_bytes()
        try:
            metadata = read_png_metadata(data)
        except TiAssetInvalidImageError as exc:
            problems.append(f"{filename}: {exc}")
            continue

        if not metadata.has_alpha:
            problems.append(
                f"{filename}: no alpha/transparency capability (color_type={metadata.color_type})"
            )
            continue

        validated[state] = (data, metadata)

    if problems:
        raise TiAssetIngestionError(
            "canonical Tí asset ingestion validation failed:\n  " + "\n  ".join(sorted(problems))
        )

    return validated


def ingest_ti_asset_set(
    engine: Engine,
    file_store: TiAssetFileStore,
    source_dir: Path | str,
    version: str,
    notes: str | None = None,
    activate: bool = False,
) -> TiAssetSet:
    """Validate, copy, register, and (optionally) activate one complete
    canonical Tí asset set from source_dir.

    Order of operations, and why it's safe:
      1. Reject a version that already exists in storage (a conflict, not
         silently overwritten or given a second identity) -- checked
         before touching the source directory at all.
      2. Validate the full source directory (existence, PNG validity,
         dimensions, alpha capability) for all 8 states -- no database or
         file-store mutation has happened yet.
      3. Copy each validated file into file_store under a freshly-minted
         asset_set_id, using the existing {asset_set_id}/{version}/
         {state}.ext convention.
      4. Build and validate the TiAssetSet domain object (re-checks
         completeness/no-duplicates structurally, cheap insurance).
      5. save_ti_asset_set persists it in one DB transaction, atomically
         deactivating any other active set first if activate=True.

    If a copy in step 3 fails partway through (e.g. disk full), the
    exception propagates immediately and save_ti_asset_set in step 5 is
    never reached -- no TiAssetSet row (active or not) is ever written for
    a failed ingestion. Files already copied before that failure are,
    honestly, NOT cleaned up: Phase 21.1's "no automatic repair" policy
    applies here too, so a failed ingestion can leave orphan files under
    file_store.root. This is safe (a fresh uuid4() means a retry can never
    collide with those orphans) but not tidy -- a human must remove them
    manually if disk space matters.
    """
    if any(existing.version == version for existing in list_ti_asset_sets(engine)):
        raise TiAssetIngestionError(
            f"version {version!r} already exists in storage; choose a different "
            f"--version rather than re-ingesting the same one"
        )

    source_dir = Path(source_dir)
    validated = _validate_source_directory(source_dir)

    asset_set_id = uuid4()
    assets: list[TiAsset] = []
    for state, (data, metadata) in validated.items():
        relative_path = build_relative_path(asset_set_id, version, state, VisualOutputFormat.PNG)
        file_store.write(relative_path, data)  # may raise TiAssetWriteError partway through

        assets.append(
            TiAsset(
                asset_set_id=asset_set_id,
                state=state,
                asset_type=TiAssetType.STILL,
                relative_path=relative_path,
                output_format=VisualOutputFormat.PNG,
                mime_type=TI_ASSET_MIME_BY_FORMAT[VisualOutputFormat.PNG],
                width=metadata.width,
                height=metadata.height,
                transparent_background=True,
            )
        )

    asset_set = TiAssetSet(
        id=asset_set_id,
        version=version,
        is_active=activate,
        assets=assets,
        notes=notes,
        created_at=datetime.now(timezone.utc),
    )
    save_ti_asset_set(engine, asset_set)
    return asset_set
