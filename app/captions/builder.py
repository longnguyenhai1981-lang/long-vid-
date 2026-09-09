"""Pure, deterministic caption text/timing derivation (Phase 31).

`build_caption_manifest` never touches a database, a filesystem, a
subprocess, an LLM, or a speech-to-text engine -- it only re-joins
identities Phase 27 already established:

    TimelineSegment.narration[i].chunk_id
        -> VoicePlan.chunks[chunk_id].line_ids
        -> ScriptPlan's own ScriptLine.text for each of those ids

When a VoiceChunk spans more than one ScriptLine, its cue's own text is
those lines' texts joined with a single ASCII space, in `line_ids`' own
list order -- never re-punctuated, re-capitalized, or otherwise altered.
For the common single-line-per-chunk case this join is the identity
function: the cue's text is byte-for-byte that one ScriptLine.text,
which is exactly requirement #28's own source-text-fidelity contract.

Exactly one CaptionCue per TimelineAudioRef (requirement #5) -- never
merged across chunks even when they share a visual segment, never split
by words/heuristics. Timing is copied from the SAME cumulative-duration
arithmetic TimelineBuilder itself already used to compute each segment's
own start_ms/end_ms (app/renderers/timeline/builder.py): the first cue
in a segment starts at `segment.start_ms`; each next cue starts exactly
where the previous narration ref's own duration_ms ends. A defensive
check (requirement #6) confirms the accumulated total exactly equals
`segment.duration_ms` -- this can only fail if TimelineManifest itself is
internally inconsistent, since this is the identical sum
TimelineBuilder used to derive that same duration_ms in the first place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.captions.errors import (
    CaptionTimingOverflowError,
    MissingCaptionSourceTextError,
    UnknownCaptionChunkReferenceError,
)
from app.models.caption import CaptionCue, CaptionManifest
from app.models.script import ScriptPlan
from app.models.timeline import TimelineManifest
from app.models.voice import VoicePlan


def build_caption_manifest(
    project_id: UUID,
    timeline_manifest: TimelineManifest,
    voice_plan: VoicePlan,
    script_plan: ScriptPlan,
) -> CaptionManifest:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in voice_plan.chunks}
    line_text_by_id = {line.line_id: line.text for beat in script_plan.beats for line in beat.lines}

    cues: list[CaptionCue] = []
    for segment in timeline_manifest.segments:
        cumulative_ms = 0
        for ref in segment.narration:
            chunk = chunks_by_id.get(ref.chunk_id)
            if chunk is None:
                raise UnknownCaptionChunkReferenceError(
                    f"TimelineSegment {segment.segment_id!r} references voice chunk "
                    f"{ref.chunk_id!r}, which does not exist in the current VoicePlan"
                )

            missing_line_ids = [lid for lid in chunk.line_ids if lid not in line_text_by_id]
            if missing_line_ids:
                raise MissingCaptionSourceTextError(
                    f"VoiceChunk {chunk.chunk_id!r} references script line(s) "
                    f"{missing_line_ids} with no matching ScriptLine in the current "
                    f"ScriptPlan"
                )
            text = " ".join(line_text_by_id[line_id] for line_id in chunk.line_ids)

            start_ms = segment.start_ms + cumulative_ms
            end_ms = start_ms + ref.duration_ms
            cues.append(
                CaptionCue(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_ms=ref.duration_ms,
                    text=text,
                    script_line_ids=list(chunk.line_ids),
                    voice_chunk_id=chunk.chunk_id,
                    render_job_id=ref.render_job_id,
                )
            )
            cumulative_ms += ref.duration_ms

        if cumulative_ms != segment.duration_ms:
            raise CaptionTimingOverflowError(
                f"TimelineSegment {segment.segment_id!r}'s narration refs total "
                f"{cumulative_ms}ms, but the segment's own duration_ms is "
                f"{segment.duration_ms}ms"
            )

    return CaptionManifest(
        project_id=project_id,
        timeline_manifest_id=timeline_manifest.id,
        script_plan_id=script_plan.id,
        voice_plan_id=voice_plan.id,
        voice_render_manifest_id=timeline_manifest.voice_render_manifest_id,
        total_duration_ms=timeline_manifest.total_duration_ms,
        cues=cues,
        created_at=datetime.now(timezone.utc),
    )
