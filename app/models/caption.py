"""CaptionManifest: a deterministic subtitle/caption timing artifact
derived from an already-locked TimelineManifest and the exact authored
script/voice text it was built from (Phase 31).

Caption text is never transcribed, inferred, or generated -- every
CaptionCue's own `text` is the literal ScriptLine.text (or the
space-joined text of several ScriptLines when one VoiceChunk spans more
than one) that VoicePlan.chunks[...].line_ids already declares was
spoken for that chunk's own rendered narration clip -- see
app/captions/builder.py for the exact join. No normalization, no
grammar/punctuation correction, and no automatic line-wrapping is ever
applied here -- CaptionCue.text is exactly what an author wrote (Phase
31's own requirement #28: caption text == exact authored narration
text).

Timing is never estimated from character count or audio waveform
analysis: each CaptionCue's start_ms/end_ms is derived directly from the
SAME TimelineAudioRef.duration_ms values TimelineManifest's own segments
already carry (Phase 27), so a CaptionManifest built from a given
TimelineManifest can never disagree with the video/audio actually
encoded from it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank


class CaptionCue(MotilyModel):
    """One caption's exact text and timing -- always traceable back to the
    real VoiceChunk/ScriptLine(s) it was spoken from. Styling/presentation
    (font, position, wrapping) is deliberately NOT stored here -- see
    app/captions/models.py's CaptionRenderSettings for that separate,
    execution-side concern."""

    id: UUID = Field(default_factory=uuid4)
    start_ms: int
    end_ms: int
    duration_ms: int
    text: str
    script_line_ids: list[str] = Field(min_length=1)
    voice_chunk_id: str
    render_job_id: str

    @model_validator(mode="after")
    def _check_invariants(self) -> "CaptionCue":
        if self.start_ms < 0:
            raise ValueError(f"start_ms must be >= 0; got {self.start_ms}")
        if self.end_ms <= self.start_ms:
            raise ValueError(
                f"end_ms ({self.end_ms}) must be greater than start_ms ({self.start_ms})"
            )
        if self.duration_ms != self.end_ms - self.start_ms:
            raise ValueError(
                f"duration_ms ({self.duration_ms}) must equal end_ms - start_ms "
                f"({self.end_ms - self.start_ms})"
            )
        non_blank(self.text, "text")
        non_blank(self.voice_chunk_id, "voice_chunk_id")
        non_blank(self.render_job_id, "render_job_id")
        return self


class CaptionManifest(MotilyModel):
    """One project's complete set of caption cues for one TimelineManifest
    -- mirrors TimelineManifest's own shape (id/project_id/upstream ids/
    total_duration_ms/created_at) closely, since captions are a direct,
    lossless re-expression of that same timeline's own timing."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    timeline_manifest_id: UUID
    script_plan_id: UUID
    voice_plan_id: UUID
    voice_render_manifest_id: UUID
    total_duration_ms: int
    cues: list[CaptionCue] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "CaptionManifest":
        if self.total_duration_ms <= 0:
            raise ValueError("total_duration_ms must be > 0")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

        ids_seen: set[UUID] = set()
        for cue in self.cues:
            if cue.id in ids_seen:
                raise ValueError(f"duplicate CaptionCue id: {cue.id}")
            ids_seen.add(cue.id)
            if cue.end_ms > self.total_duration_ms:
                raise ValueError(
                    f"CaptionCue {cue.id} ends at {cue.end_ms}ms, beyond this manifest's "
                    f"own total_duration_ms ({self.total_duration_ms}ms)"
                )

        for earlier, later in zip(self.cues, self.cues[1:]):
            if later.start_ms < earlier.end_ms:
                raise ValueError(
                    f"CaptionCue {later.id} starts at {later.start_ms}ms, before the "
                    f"preceding cue {earlier.id} ends at {earlier.end_ms}ms -- cues must "
                    f"be chronological and non-overlapping (narration itself never "
                    f"overlaps, so this indicates a caption-layer construction bug)"
                )
        return self
