"""Manual visual-evaluation utility for the Phase 22 Tí compositor.

Composites three canonical Tí states from the active TiAssetSet onto ONE
existing, already-present background image, so a human can look at the
placement/scale/alpha-compositing results:

    NEUTRAL  -> BOTTOM_LEFT
    CURIOUS  -> BOTTOM_RIGHT
    PANIC    -> CENTER_RIGHT

This is a manual viewing aid only, exactly like scripts/evaluate_gemini_
image.py and scripts/evaluate_cloudflare_image.py:

- No automated aesthetic/quality scoring, no LLM judge.
- No new background image is generated -- this script reuses
  data/cloudflare_image_evaluation/L1_ESTABLISH.jpg (Phase 20.2's
  Cloudflare evaluation output: an empty suspension-bridge establishing
  shot, 1024x1024, with no Tí already composited into it), the one
  existing local background this repository has that both (a) isn't
  itself a Tí asset and (b) isn't a background someone would need to
  regenerate to run this script.
- Tí comes exclusively from the active TiAssetSet via TiAssetRetriever/
  TiCompositor -- no AI image generation, no VisualProvider call, no LLM
  placement.

Usage:

    python scripts/evaluate_ti_compositor.py
    python scripts/evaluate_ti_compositor.py --out-dir data/ti_compositor_evaluation_2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.models.ti_assets import TiState
from app.storage.database import DEFAULT_DB_PATH, init_database
from app.storage.errors import TiAssetSetNotFoundError
from app.ti_assets.errors import TiAssetError
from app.ti_assets.retriever import SqliteTiAssetRetriever
from app.ti_assets.storage import DEFAULT_TI_ASSET_ROOT, TiAssetFileStore
from app.ti_compositor.compositor import TiCompositor
from app.ti_compositor.errors import TiCompositorError
from app.ti_compositor.models import TiAnchor, TiCompositeRequest, TiPlacement, TiScalePolicy

DEFAULT_BACKGROUND = Path("data/cloudflare_image_evaluation/L1_ESTABLISH.jpg")

# (TiState, TiAnchor, output filename) -- the exact 3 evaluation outputs
# Phase 22 asks for, no more.
EVALUATION_CASES: list[tuple[TiState, TiAnchor, str]] = [
    (TiState.NEUTRAL, TiAnchor.BOTTOM_LEFT, "neutral_bottom_left.png"),
    (TiState.CURIOUS, TiAnchor.BOTTOM_RIGHT, "curious_bottom_right.png"),
    (TiState.PANIC, TiAnchor.CENTER_RIGHT, "panic_center_right.png"),
]

RELATIVE_HEIGHT = 0.35
MARGIN_PX = 24


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--background",
        type=Path,
        default=DEFAULT_BACKGROUND,
        help=f"Existing background image to composite onto (default: {DEFAULT_BACKGROUND})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/ti_compositor_evaluation"),
        help="Directory to write the 3 composited PNGs into",
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

    if not args.background.is_file():
        print(
            f"No existing background image found at {args.background}. "
            "Per Phase 22's instructions, this script does not generate a new "
            "background -- stopping.",
            file=sys.stderr,
        )
        return 1

    engine = init_database(args.db)
    file_store = TiAssetFileStore(args.asset_root)
    retriever = SqliteTiAssetRetriever(engine, file_store)
    compositor = TiCompositor(retriever)

    try:
        active_set = retriever.get_active_asset_set()
    except TiAssetSetNotFoundError as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Active TiAssetSet: {active_set.id} version {active_set.version!r}")
    print(f"Background: {args.background.resolve()}")
    print(f"Output directory: {args.out_dir.resolve()}")
    print()

    results = []
    for ti_state, anchor, filename in EVALUATION_CASES:
        request = TiCompositeRequest(
            background_path=args.background,
            output_path=args.out_dir / filename,
            placement=TiPlacement(
                anchor=anchor,
                scale=TiScalePolicy(relative_height=RELATIVE_HEIGHT),
                margin=MARGIN_PX,
            ),
            ti_state=ti_state,
        )
        try:
            result = compositor.composite(request)
        except (TiCompositorError, TiAssetError) as exc:
            print(f"  {ti_state.value} @ {anchor.value}: FAILED ({exc})")
            continue

        print(
            f"  {ti_state.value} @ {anchor.value}: {result.output_path} "
            f"(Tí {result.ti_rendered_width}x{result.ti_rendered_height} at "
            f"({result.placement_x},{result.placement_y}), clamped={result.clamped})"
        )
        results.append(result)

    print()
    print(f"Composited {len(results)}/{len(EVALUATION_CASES)} evaluation output(s). Look and decide manually --")
    print("this script does not rank or score placement quality.")
    return 0 if len(results) == len(EVALUATION_CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
