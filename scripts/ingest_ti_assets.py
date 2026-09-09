"""CLI for ingesting a human-supplied canonical Tí asset directory (Phase
21.1) into the local SQLite database + Tí asset file store.

This is registration only -- it never generates, edits, or repairs an
image. The source directory must already contain exactly these 8 files,
each a PNG with an alpha channel or tRNS-based transparency:

    NEUTRAL.png    CURIOUS.png    SKEPTICAL.png   EXCITED.png
    SERIOUS.png    DEADPAN.png    PANIC.png       LOW_ENERGY.png

The whole directory is validated before anything is written -- a missing,
misnamed, invalid, or non-transparent file fails the run cleanly with no
partial database or file-store state. This script does not, and will
never, fabricate placeholder Tí artwork: until a human supplies real files
matching every one of the 8 names above, every run fails.

Usage:

    python scripts/ingest_ti_assets.py --source path/to/ti_v1 --version v1
    python scripts/ingest_ti_assets.py --source path/to/ti_v1 --version v1 --activate
    python scripts/ingest_ti_assets.py --source path/to/ti_v2 --version v2 --notes "redesigned eyes" --activate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.storage.database import init_database, DEFAULT_DB_PATH
from app.ti_assets.errors import TiAssetError
from app.ti_assets.ingest import ingest_ti_asset_set
from app.ti_assets.storage import DEFAULT_TI_ASSET_ROOT, TiAssetFileStore


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--source", type=Path, required=True, help="Directory containing the 8 required PNG files"
    )
    parser.add_argument(
        "--version", required=True, help="Version label for this canonical asset set, e.g. v1"
    )
    parser.add_argument(
        "--notes", default=None, help="Optional free-text notes to store with this asset set"
    )
    parser.add_argument(
        "--activate", action="store_true", help="Make this the sole active canonical asset set"
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=DEFAULT_TI_ASSET_ROOT,
        help=f"Tí asset file store root (default: {DEFAULT_TI_ASSET_ROOT})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = _parse_args(argv if argv is not None else sys.argv[1:])

    engine = init_database(args.db)
    file_store = TiAssetFileStore(args.asset_root)

    try:
        asset_set = ingest_ti_asset_set(
            engine,
            file_store,
            source_dir=args.source,
            version=args.version,
            notes=args.notes,
            activate=args.activate,
        )
    except TiAssetError as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1

    print(f"Ingested TiAssetSet {asset_set.id} version {asset_set.version!r}")
    print(f"  states: {', '.join(asset.state.value for asset in asset_set.assets)}")
    print(f"  active: {asset_set.is_active}")
    print(f"  stored under: {file_store.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
