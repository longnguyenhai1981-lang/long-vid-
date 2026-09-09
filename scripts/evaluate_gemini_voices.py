"""Manual Vietnamese voice-evaluation utility for Gemini TTS (Phase 18).

Renders the SAME short Vietnamese sample once per voice in a small,
explicit list, so a human can listen and compare. This is a manual
listening aid only:

- No automated naturalness scoring.
- No LLM (or any other automated judge) ranks the results.
- No canonical Tí voice is chosen by this script -- a human decides that
  later, after listening.

Usage:

    set GEMINI_API_KEY=...                 (Windows)
    export GEMINI_API_KEY=...              (bash)
    python scripts/evaluate_gemini_voices.py
    python scripts/evaluate_gemini_voices.py --voices Puck,Kore --text "Ủa, cái gì vậy ta?"
    python scripts/evaluate_gemini_voices.py --out-dir data/voice_eval_2

Keep the sample short and the voice list explicit -- this makes one real
API call per voice, and real API quota is a real, external, and
finite/priced resource.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.audio.errors import TTSError
from app.audio.models import TTSRequest
from app.audio.providers.gemini import GeminiTTSConfig, GeminiTTSConfigurationError, GeminiTTSProvider

# A small starting shortlist for manual listening -- not a ranking, and not
# an exhaustive list of every Gemini prebuilt voice.
DEFAULT_VOICES = ["Puck", "Achird", "Zubenelgenubi", "Iapetus"]
DEFAULT_TEXT = "Chào! Một Tí Lý đây."
DEFAULT_OUT_DIR = Path("data/gemini_voice_evaluation")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voices",
        type=str,
        default=",".join(DEFAULT_VOICES),
        help=f"Comma-separated Gemini prebuilt voice names (default: {','.join(DEFAULT_VOICES)})",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=DEFAULT_TEXT,
        help="Short Vietnamese sample text to render with every voice",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Directory to write one WAV file per voice into (default: {DEFAULT_OUT_DIR})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # The sample text and voice names are Vietnamese; a Windows console's
    # default codepage (e.g. cp1252) cannot encode them, so force UTF-8
    # regardless of the host terminal's default.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    if not voices:
        print("No voices given -- pass --voices Name1,Name2,...", file=sys.stderr)
        return 1

    try:
        provider = GeminiTTSProvider(GeminiTTSConfig())
    except GeminiTTSConfigurationError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f'Sample text: "{args.text}"')
    print(f"Voices ({len(voices)}): {', '.join(voices)}")
    print(f"Output directory: {args.out_dir.resolve()}")
    print()

    results = []
    for voice_id in voices:
        request = TTSRequest(
            text=args.text,
            voice_id=voice_id,
            voice_state="NEUTRAL",
            pace="NORMAL",
            energy="MEDIUM",
            output_format="WAV",
        )
        try:
            response = provider.synthesize(request)
        except TTSError as exc:
            print(f"  {voice_id}: FAILED ({exc})")
            continue

        out_path = args.out_dir / f"{voice_id}.wav"
        out_path.write_bytes(response.audio_bytes)
        duration = f"{response.duration_seconds:.2f}s" if response.duration_seconds else "unknown"
        print(f"  {voice_id}: {out_path} ({duration})")
        results.append((voice_id, out_path))

    print()
    print(f"Rendered {len(results)}/{len(voices)} voice(s). Listen and decide manually --")
    print("this script does not rank or recommend a voice.")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
