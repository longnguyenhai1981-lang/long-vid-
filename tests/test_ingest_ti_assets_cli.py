from __future__ import annotations

from app.models.ti_assets import TiState
from app.storage.database import init_database
from app.storage.ti_assets import get_active_ti_asset_set
from scripts.ingest_ti_assets import main
from tests._png_helpers import make_png_bytes


def _write_full_source_dir(source_dir):
    source_dir.mkdir(parents=True, exist_ok=True)
    for state in TiState:
        (source_dir / f"{state.value}.png").write_bytes(make_png_bytes(color_type=6))


def test_cli_success_with_activate(tmp_path):
    source_dir = tmp_path / "source"
    _write_full_source_dir(source_dir)
    db_path = tmp_path / "motily.db"
    asset_root = tmp_path / "ti_assets"

    exit_code = main(
        [
            "--source",
            str(source_dir),
            "--version",
            "v1",
            "--activate",
            "--db",
            str(db_path),
            "--asset-root",
            str(asset_root),
        ]
    )

    assert exit_code == 0
    engine = init_database(db_path)
    active = get_active_ti_asset_set(engine)
    assert active.version == "v1"
    assert active.is_active is True


def test_cli_fails_clearly_with_missing_files(tmp_path, capsys):
    empty_source = tmp_path / "empty_source"
    empty_source.mkdir()
    db_path = tmp_path / "motily.db"
    asset_root = tmp_path / "ti_assets"

    exit_code = main(
        [
            "--source",
            str(empty_source),
            "--version",
            "v1",
            "--db",
            str(db_path),
            "--asset-root",
            str(asset_root),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Ingestion failed" in captured.err
