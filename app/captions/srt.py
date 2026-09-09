"""Deterministic UTF-8 SRT serialization (Phase 31 requirement #11) and
parsing (Phase 32 requirement #15, added for QC's own text/timing
identity check).

`render_srt` is a pure function of a CaptionManifest -- no filesystem
access, no styling tags, no HTML-escaping (SRT is plain text; ordinary
punctuation/Unicode never needs escaping for it). Cue numbering starts
at 1 and follows `manifest.cues`' own list order -- CaptionManifest's own
validator already enforces chronological, non-overlapping cues, so this
never re-sorts or re-derives order.

Final newline policy: the returned string is a sequence of blocks
(`<number>\\n<start> --> <end>\\n<text>`) joined by exactly one blank
line (`\\n\\n`), with exactly one trailing `\\n` after the very last
block's own text -- i.e. no superfluous trailing blank line after the
last cue, matching the shape most SRT consumers (including ffmpeg's own
`subtitles` filter) expect.

`parse_srt` is the exact inverse of `render_srt` for this project's own
deterministic output shape (never a general-purpose SRT parser tolerant
of arbitrary third-party quirks -- QC only ever parses SRT this project
itself produced). It is used exclusively by app/media_qc/rules.py to
re-read an exported SRT file back into structured cues for comparison
against the CaptionManifest it should have been generated from -- never
by app/captions/burn_in.py or any production caption-execution path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.caption import CaptionManifest

_TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def render_srt(manifest: CaptionManifest) -> str:
    blocks = [
        f"{index}\n{_format_timestamp(cue.start_ms)} --> {_format_timestamp(cue.end_ms)}\n{cue.text}"
        for index, cue in enumerate(manifest.cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def _format_timestamp(total_ms: int) -> str:
    """`HH:MM:SS,mmm` -- hours are never capped or wrapped, so timelines
    beyond 1 hour (or even 24 hours) format correctly with no special
    casing."""
    hours, remainder_ms = divmod(total_ms, 3_600_000)
    minutes, remainder_ms = divmod(remainder_ms, 60_000)
    seconds, milliseconds = divmod(remainder_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


@dataclass(frozen=True)
class ParsedSrtCue:
    index: int
    start_ms: int
    end_ms: int
    text: str


class SrtParseError(ValueError):
    """Raised when text does not match this project's own deterministic
    `render_srt` output shape closely enough to parse -- never silently
    skips a malformed block."""


def parse_srt(text: str) -> list[ParsedSrtCue]:
    stripped = text.strip("\n")
    if not stripped:
        return []
    blocks = stripped.split("\n\n")
    cues: list[ParsedSrtCue] = []
    for block_number, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 3:
            raise SrtParseError(f"SRT block {block_number} has too few lines: {block!r}")
        try:
            index = int(lines[0].strip())
        except ValueError as exc:
            raise SrtParseError(f"SRT block {block_number} has a non-integer cue number: {lines[0]!r}") from exc

        start_str, separator, end_str = lines[1].partition(" --> ")
        if not separator:
            raise SrtParseError(f"SRT block {block_number} has a malformed timestamp line: {lines[1]!r}")
        start_ms = _parse_timestamp(start_str, block_number)
        end_ms = _parse_timestamp(end_str, block_number)

        cue_text = "\n".join(lines[2:])
        cues.append(ParsedSrtCue(index=index, start_ms=start_ms, end_ms=end_ms, text=cue_text))
    return cues


def _parse_timestamp(raw: str, block_number: int) -> int:
    match = _TIMESTAMP_RE.match(raw.strip())
    if not match:
        raise SrtParseError(f"SRT block {block_number} has an invalid timestamp: {raw!r}")
    hours, minutes, seconds, milliseconds = (int(group) for group in match.groups())
    return hours * 3_600_000 + minutes * 60_000 + seconds * 1000 + milliseconds
