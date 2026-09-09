from __future__ import annotations

import pytest

from app.models.ti_assets import TiState
from app.storage.ti_assets import get_active_ti_asset_set, list_ti_asset_sets
from app.ti_assets.errors import TiAssetIngestionError
from app.ti_assets.ingest import ingest_ti_asset_set
from app.ti_assets.storage import TiAssetFileStore
from tests._png_helpers import make_garbage_bytes, make_png_bytes


def _write_full_source_dir(source_dir, color_type=6, include_trns=False):
    source_dir.mkdir(parents=True, exist_ok=True)
    for state in TiState:
        data = make_png_bytes(width=16, height=16, color_type=color_type, include_trns=include_trns)
        (source_dir / f"{state.value}.png").write_bytes(data)


def test_successful_eight_file_ingestion(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    file_store = TiAssetFileStore(tmp_path / "store")

    asset_set = ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    assert len(asset_set.assets) == len(TiState)
    assert {a.state for a in asset_set.assets} == set(TiState)
    for asset in asset_set.assets:
        assert file_store.exists(asset.relative_path)
        assert asset.transparent_background is True
        assert asset.width == 16
        assert asset.height == 16


def test_missing_required_file_rejected(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    (source_dir / "PANIC.png").unlink()
    file_store = TiAssetFileStore(tmp_path / "store")

    with pytest.raises(TiAssetIngestionError, match="PANIC.png"):
        ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    assert list_ti_asset_sets(engine) == []


def test_wrong_filename_is_treated_as_missing(tmp_path, engine):
    # Not a case variant (NTFS/Windows paths are case-insensitive, so
    # "panic.png" and "PANIC.png" are the same file there) -- a genuinely
    # different name, proving there is no fuzzy/near-match filename
    # inference at all.
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    (source_dir / "PANIC.png").unlink()
    (source_dir / "PANIC_v2.png").write_bytes(make_png_bytes(color_type=6))
    file_store = TiAssetFileStore(tmp_path / "store")

    with pytest.raises(TiAssetIngestionError, match="PANIC.png"):
        ingest_ti_asset_set(engine, file_store, source_dir, version="v1")


def test_invalid_png_rejected(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    (source_dir / "NEUTRAL.png").write_bytes(make_garbage_bytes())
    file_store = TiAssetFileStore(tmp_path / "store")

    with pytest.raises(TiAssetIngestionError, match="NEUTRAL.png"):
        ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    assert list_ti_asset_sets(engine) == []


def test_png_without_alpha_rejected(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir, color_type=2, include_trns=False)  # opaque RGB
    file_store = TiAssetFileStore(tmp_path / "store")

    with pytest.raises(TiAssetIngestionError, match="alpha"):
        ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    assert list_ti_asset_sets(engine) == []


def test_png_with_trns_transparency_accepted(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir, color_type=2, include_trns=True)
    file_store = TiAssetFileStore(tmp_path / "store")

    asset_set = ingest_ti_asset_set(engine, file_store, source_dir, version="v1")
    assert len(asset_set.assets) == len(TiState)


def test_reports_every_problem_not_just_the_first(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    (source_dir / "PANIC.png").unlink()
    (source_dir / "NEUTRAL.png").write_bytes(make_garbage_bytes())
    file_store = TiAssetFileStore(tmp_path / "store")

    with pytest.raises(TiAssetIngestionError) as exc_info:
        ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    message = str(exc_info.value)
    assert "PANIC.png" in message
    assert "NEUTRAL.png" in message


def test_missing_source_directory_rejected(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path / "store")
    with pytest.raises(TiAssetIngestionError, match="does not exist"):
        ingest_ti_asset_set(engine, file_store, tmp_path / "nope", version="v1")


def test_duplicate_version_conflict_rejected(tmp_path, engine):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    file_store = TiAssetFileStore(tmp_path / "store")
    ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    other_source = tmp_path / "source2"
    _write_full_source_dir(other_source)
    with pytest.raises(TiAssetIngestionError, match="v1"):
        ingest_ti_asset_set(engine, file_store, other_source, version="v1")

    assert len(list_ti_asset_sets(engine)) == 1


def test_activate_false_leaves_current_active_set_unchanged(tmp_path, engine):
    source_v1 = tmp_path / "source_v1"
    _write_full_source_dir(source_v1)
    file_store = TiAssetFileStore(tmp_path / "store")
    v1 = ingest_ti_asset_set(engine, file_store, source_v1, version="v1", activate=True)

    source_v2 = tmp_path / "source_v2"
    _write_full_source_dir(source_v2)
    ingest_ti_asset_set(engine, file_store, source_v2, version="v2", activate=False)

    active = get_active_ti_asset_set(engine)
    assert active.id == v1.id
    assert active.version == "v1"


def test_activate_true_makes_ingested_set_the_sole_active_set(tmp_path, engine):
    source_v1 = tmp_path / "source_v1"
    _write_full_source_dir(source_v1)
    file_store = TiAssetFileStore(tmp_path / "store")
    ingest_ti_asset_set(engine, file_store, source_v1, version="v1", activate=True)

    source_v2 = tmp_path / "source_v2"
    _write_full_source_dir(source_v2)
    v2 = ingest_ti_asset_set(engine, file_store, source_v2, version="v2", activate=True)

    active = get_active_ti_asset_set(engine)
    assert active.id == v2.id
    assert active.version == "v2"


def test_db_contains_metadata_and_path_only_never_bytes(tmp_path, engine):
    from sqlalchemy import select

    from app.storage.orm import TiAssetSetRow

    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    file_store = TiAssetFileStore(tmp_path / "store")
    asset_set = ingest_ti_asset_set(engine, file_store, source_dir, version="v1")

    with engine.connect() as conn:
        payload = conn.execute(
            select(TiAssetSetRow.payload_json).where(TiAssetSetRow.asset_set_id == str(asset_set.id))
        ).scalar_one()

    assert "asset_bytes" not in payload
    # The payload must be small metadata, nowhere near real image byte sizes.
    assert len(payload) < 10_000


def test_no_provider_or_llm_imports_in_ingest_module():
    import app.ti_assets.ingest as ingest_module

    source = ingest_module.__file__
    with open(source, "r", encoding="utf-8") as f:
        text = f.read()
    for forbidden in ("google.genai", "app.llm", "app.visual.providers", "httpx"):
        assert forbidden not in text
