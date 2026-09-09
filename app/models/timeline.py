"""TimelineManifest: a deterministic, executable static-video timeline
built from an already-locked ScriptPlan/VoicePlan/VisualPlan/AssemblyPlan
chain and their already-rendered VoiceRenderManifest/VisualRenderManifest
outputs (Phase 27).

This is the final planning-adjacent artifact before real video encoding --
it describes exactly what is shown, when, and what plays, in integer
milliseconds, but renders nothing itself: no ffmpeg call, no pixel/audio
mixing, no CapCut project. See app/renderers/timeline/builder.py for how
it is built, and docs/TECHNICAL_SPEC_v0.1.md's Phase 27 section for the
full design rationale (timebase choice, voice-duration-as-timing-
authority, the TransitionIntent -> TimelineTransitionType downgrade
mapping, and the music-state-change cue derivation policy).

Phase 29 adds TimelineSegment.visual_motion (VisualMotionType, default
STATIC) -- a deliberately small, explicitly-authored, deterministic
camera-motion vocabulary that app/video_encoder/'s VideoEncoder executes
at encode time. See that package's docstrings for the exact FFmpeg filter
mechanics; this field only carries the authored INTENT, never any
rendering detail. Phase 29 also makes TimelineTransitionType.CROSSFADE
executable (app/video_encoder/), not merely representable -- the enum
itself is unchanged.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, MusicState, TransitionIntent, VisualMediaType, non_blank
from app.models.ti_assets import TiState


class TimelineTransitionType(str, Enum):
    """The fixed MVP transition vocabulary a TimelineSegment itself may
    declare (Phase 27) -- deliberately smaller than AssemblyPlan's own
    5-member TransitionIntent (Phase 15): no motion/zoom/pan/shake/Ken-
    Burns TRANSITION (i.e. a blend/effect that spans the join between two
    segments) can be executed by this timeline. Every TransitionIntent is
    deterministically downgraded to one of these three (see
    app/renderers/timeline/builder.py's _TRANSITION_TYPE_BY_INTENT). The
    original, full-fidelity TransitionIntent is never discarded --
    TimelineSegment.source_transition_in/out keeps it alongside. As of
    Phase 29, CROSSFADE is executed for real by app/video_encoder/ (a
    deterministic dissolve) rather than rejected -- see
    VisualMotionType below for the separate, per-segment camera-motion
    vocabulary Phase 29 also adds."""

    CUT = "CUT"
    HOLD = "HOLD"
    CROSSFADE = "CROSSFADE"


class VisualMotionType(str, Enum):
    """A deliberately tiny, deterministic per-segment camera-motion
    vocabulary (Phase 29) -- frame-level presentation only, never a
    scientific/narrative decision, which is why this lives on
    TimelineSegment (execution) rather than ScriptPlan/VisualPlan/
    AssemblyPlan (content/planning). Motion is always explicitly
    authored (see app/renderers/timeline/models.py's
    TimelineBuilderInput.visual_motions) -- never inferred from content,
    never LLM-chosen, never computer-vision/saliency-based. STATIC is the
    default and requires no motion filter at all; every other member
    spans exactly its segment's own authored duration_ms, never changing
    segment start/end timing. No rotation, shake, bounce, arbitrary x/y
    keyframes, path animation, perspective, parallax, or puppet
    animation -- see app/video_encoder/encoder.py for the exact FFmpeg
    filter each member maps to."""

    STATIC = "STATIC"
    SLOW_ZOOM_IN = "SLOW_ZOOM_IN"
    SLOW_ZOOM_OUT = "SLOW_ZOOM_OUT"
    PAN_LEFT = "PAN_LEFT"
    PAN_RIGHT = "PAN_RIGHT"


class TimelineCueType(str, Enum):
    """A lightweight deterministic event vocabulary (Phase 27). CUT and
    STATIC_HOLD are defined for a future consumer that prefers one flat
    timestamped event stream over walking TimelineSegment.transition_in/
    out itself, but this phase's own TimelineBuilder never emits them --
    doing so would duplicate information transition_in/out already
    carries per-segment. Only the MUSIC_*/SFX_TRIGGER members are ever
    produced by this phase's builder (see its music-state-change and
    sfx_opportunity derivation)."""

    MUSIC_BED_START = "MUSIC_BED_START"
    MUSIC_DUCK = "MUSIC_DUCK"
    MUSIC_LIFT = "MUSIC_LIFT"
    MUSIC_BED_END = "MUSIC_BED_END"
    SFX_TRIGGER = "SFX_TRIGGER"
    CUT = "CUT"
    STATIC_HOLD = "STATIC_HOLD"


class TimelineVisualSourceStatus(str, Enum):
    """Mirrors the RenderedVisualAsset/VisualRenderRequirement duality
    (app/models/visual_render.py) a TimelineVisualRef was resolved from --
    the timeline layer never triggers rendering, so a beat that only ever
    produced a requirement (e.g. a standalone canonical Tí reference, an
    ASSET_REUSE reuse_key, or an EXTERNAL_REQUIRED placeholder) is still
    representable here, for a future editor to place by hand."""

    RENDERED = "RENDERED"
    REQUIREMENT = "REQUIREMENT"


class TimelineVisualRef(MotilyModel):
    """Exactly one primary visual presentation for one TimelineSegment,
    resolved from VisualRenderManifest -- never re-rendered, never
    regenerated, never a second lookup path around TiCompositor/
    DiagramRenderer/VisualLayerCompositor."""

    visual_beat_id: str
    media_type: VisualMediaType
    status: TimelineVisualSourceStatus
    file_path: str | None = None
    """Set only when status is RENDERED -- the exact
    RenderedVisualAsset.file_path, relative to VisualFileStore.root."""
    reference: str | None = None
    """Set only when status is REQUIREMENT -- the exact
    VisualRenderRequirement.reference (e.g. a canonical Tí relative_path,
    or an ASSET_REUSE reuse_key)."""
    resolved_ti_state: TiState | None = None
    width: int | None = None
    height: int | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TimelineVisualRef":
        non_blank(self.visual_beat_id, "visual_beat_id")
        if self.status is TimelineVisualSourceStatus.RENDERED:
            if self.file_path is None or self.reference is not None:
                raise ValueError("RENDERED status requires file_path and forbids reference")
        else:  # REQUIREMENT
            if self.file_path is not None:
                raise ValueError("REQUIREMENT status forbids file_path")
        if self.width is not None and self.width <= 0:
            raise ValueError("width must be > 0 when supplied")
        if self.height is not None and self.height <= 0:
            raise ValueError("height must be > 0 when supplied")
        return self


class TimelineAudioRef(MotilyModel):
    """One narration clip contributing to a TimelineSegment's total
    duration -- exact rendered voice asset, exact measured/reported
    duration. A segment may reference more than one (its
    AssemblySegment.voice_chunk_ids may span multiple VoiceChunks),
    concatenated in list order with no gap between them."""

    chunk_id: str
    render_job_id: str
    file_path: str
    duration_ms: int

    @model_validator(mode="after")
    def _check_invariants(self) -> "TimelineAudioRef":
        non_blank(self.chunk_id, "chunk_id")
        non_blank(self.render_job_id, "render_job_id")
        non_blank(self.file_path, "file_path")
        if self.duration_ms <= 0:
            raise ValueError("duration_ms must be > 0")
        return self


class TimelineSegment(MotilyModel):
    """The smallest executable timeline unit -- one AssemblySegment's
    worth of real, resolved timing/media, in integer milliseconds.
    Mirrors AssemblySegment's own beat/chunk/line identity fields exactly
    (same segment_id, same script_line_ids) for direct traceability back
    to the plan this was built from."""

    segment_id: str
    script_line_ids: list[str] = Field(min_length=1)
    start_ms: int
    end_ms: int
    duration_ms: int
    narration: list[TimelineAudioRef] = Field(min_length=1)
    visual: TimelineVisualRef
    transition_in: TimelineTransitionType
    transition_out: TimelineTransitionType
    source_transition_in: TransitionIntent
    source_transition_out: TransitionIntent
    music_state: MusicState
    visual_motion: VisualMotionType = VisualMotionType.STATIC
    """Phase 29: an explicitly-authored camera-motion instruction (see
    VisualMotionType). Defaults to STATIC -- both for a segment with no
    authored motion and, by construction, for every TimelineManifest
    persisted before Phase 29 existed (a missing field on deserialization
    picks up this same default), so old manifests remain valid without
    any migration."""
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TimelineSegment":
        non_blank(self.segment_id, "segment_id")
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
        return self


class TimelineCue(MotilyModel):
    """One lightweight, deterministic timestamped event -- automation only,
    never audio DSP and never a rendered transition effect."""

    timestamp_ms: int
    cue_type: TimelineCueType
    reference: str | None = None
    segment_id: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TimelineCue":
        if self.timestamp_ms < 0:
            raise ValueError(f"timestamp_ms must be >= 0; got {self.timestamp_ms}")
        return self


class TimelineManifest(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    script_plan_id: UUID
    voice_plan_id: UUID
    visual_plan_id: UUID
    voice_render_manifest_id: UUID
    visual_render_manifest_id: UUID
    assembly_plan_id: UUID
    total_duration_ms: int
    segments: list[TimelineSegment] = Field(min_length=1)
    cues: list[TimelineCue] = Field(default_factory=list)
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "TimelineManifest":
        if self.total_duration_ms <= 0:
            raise ValueError("total_duration_ms must be > 0")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        last_segment_end_ms = self.segments[-1].end_ms
        if self.total_duration_ms != last_segment_end_ms:
            raise ValueError(
                f"total_duration_ms ({self.total_duration_ms}) must equal the "
                f"final segment's end_ms ({last_segment_end_ms})"
            )
        return self
