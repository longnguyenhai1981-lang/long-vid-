"""Manual visual-evaluation utility for Cloudflare Workers AI (Phase 20.2).

Renders the SAME three conceptual representative GENERATED_STILL briefs
used by scripts/evaluate_gemini_image.py, through CloudflareImageProvider
(model @cf/black-forest-labs/flux-1-schnell) instead, so a human can look
at the results. This is a manual viewing aid only:

- No automated aesthetic/quality scoring.
- No LLM (or any other automated judge) ranks the results.
- No canonical visual style is chosen by this script -- a human decides
  that later, after looking, exactly like
  scripts/evaluate_gemini_voices.py does for the canonical Tí voice.

The briefs' concept/primary_focus/secondary_elements/context_elements are
intentionally identical to the Gemini evaluation's, so any perceived
difference between providers reflects the provider/model, not a changed
brief. Provider-specific prompt wrapping (style-guidance text) is still
applied independently by CloudflareImageProvider itself.

Phase 20.2 does NOT solve canonical Tí character consistency across
independently generated stills any more than Phase 20/20.1's Gemini path
does -- the Tí-present brief below only proves that a per-state
mood/expression note can be included in the prompt, not that Tí will
look the same from one generated still to the next.

Usage:

    set CLOUDFLARE_ACCOUNT_ID=...           (Windows)
    set CLOUDFLARE_API_TOKEN=...            (Windows)
    export CLOUDFLARE_ACCOUNT_ID=...        (bash)
    export CLOUDFLARE_API_TOKEN=...         (bash)
    python scripts/evaluate_cloudflare_image.py
    python scripts/evaluate_cloudflare_image.py --out-dir data/cloudflare_visual_eval_2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.visual.errors import VisualError
from app.visual.models import VisualRenderRequest
from app.visual.providers.cloudflare import (
    CloudflareImageConfig,
    CloudflareImageConfigurationError,
    CloudflareImageProvider,
)

# Same three conceptual briefs as scripts/evaluate_gemini_image.py --
# deliberately not altered to favor either provider.
DEFAULT_BRIEFS = [
    {
        "beat_id": "L1_ESTABLISH",
        "concept": "An empty suspension bridge stretching across a wide river valley under an "
        "overcast sky, establishing the scene before anything happens",
        "primary_focus": "The suspension bridge deck and towers",
        "secondary_elements": ["distant riverbanks", "a few small cars on the deck"],
        "context_elements": ["overcast daylight", "1940s American infrastructure"],
    },
    {
        "beat_id": "L2_MECHANISM",
        "concept": "The same bridge deck caught mid-twist, visibly torqueing along its length "
        "in a strong wind, showing the physical mechanism of the motion",
        "primary_focus": "The visibly twisting bridge deck",
        "secondary_elements": ["cables under visible tension", "wind-blown debris"],
        "context_elements": ["dramatic but non-photorealistic motion cues"],
    },
    {
        "beat_id": "TI_PRESENT",
        "concept": "Tí reacting to the twisting bridge from a safe vantage point on the riverbank",
        "primary_focus": "Tí's reaction",
        "secondary_elements": ["the twisting bridge in the background"],
        "context_elements": ["Tí is an observer only, not a cause of the event"],
        "ti_state": "SURPRISED",
    },
]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/cloudflare_image_evaluation"),
        help="Directory to write one JPG file per brief into",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Brief text includes diacritics (e.g. "Tí"); a Windows console's
    # default codepage cannot encode them, so force UTF-8 regardless of
    # the host terminal's default -- same fix as
    # scripts/evaluate_gemini_voices.py / evaluate_gemini_image.py.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        provider = CloudflareImageProvider(CloudflareImageConfig())
    except CloudflareImageConfigurationError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Briefs ({len(DEFAULT_BRIEFS)}): {', '.join(b['beat_id'] for b in DEFAULT_BRIEFS)}")
    print(f"Output directory: {args.out_dir.resolve()}")
    print()

    results = []
    for brief in DEFAULT_BRIEFS:
        beat_id = brief["beat_id"]
        request = VisualRenderRequest(
            render_job_id=f"{beat_id}_R1",
            beat_id=beat_id,
            media_type="GENERATED_STILL",
            concept=brief["concept"],
            primary_focus=brief["primary_focus"],
            secondary_elements=brief.get("secondary_elements", []),
            context_elements=brief.get("context_elements", []),
            ti_state=brief.get("ti_state"),
            output_format="JPG",
        )
        try:
            response = provider.render(request)
        except VisualError as exc:
            print(f"  {beat_id}: FAILED ({exc})")
            continue

        out_path = args.out_dir / f"{beat_id}.jpg"
        out_path.write_bytes(response.asset_bytes)
        print(f"  {beat_id}: {out_path}")
        results.append((beat_id, out_path))

    print()
    print(f"Rendered {len(results)}/{len(DEFAULT_BRIEFS)} still(s). Look and decide manually --")
    print("this script does not rank or recommend a visual style, and does not solve")
    print("canonical Tí character consistency across stills.")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
