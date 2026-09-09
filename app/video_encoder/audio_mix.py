"""AudioMixPlan: a pure, deterministic planning layer turning
TimelineManifest MUSIC_*/SFX_TRIGGER cues into concrete, integer-
millisecond gain regions and SFX events (Phase 30).

This module never touches the filesystem, never spawns a subprocess, and
never depends on app/video_encoder/encoder.py -- it is a pure function of
its own inputs (`cues`, `AudioAssetBindings`, `total_duration_ms`,
`VideoEncodingSettings`'s gain/ramp defaults), exactly mirroring
app/renderers/timeline/builder.py's own "describe timing, don't execute
it" split, one layer down: TimelineBuilder describes cues,
`build_audio_mix_plan` describes what those cues MEAN as mixable regions/
events, and only app/video_encoder/encoder.py's FFmpegCommandBuilder ever
turns that into a real ffmpeg filter graph. This is deliberately not a
general DAW/automation model -- it only ever produces two things: a flat
list of contiguous, non-overlapping music gain regions, and a flat list
of independent SFX trigger events.

Music state machine (requirement #14): only `transition_out`-equivalent
cue TYPES are consulted -- `TimelineCueType.MUSIC_BED_START`/`_DUCK`/
`_LIFT`/`_BED_END` -- processed in the order given (never re-sorted).
Every other cue type (`CUT`, `STATIC_HOLD`, `SFX_TRIGGER`) is inert to
this state machine, exactly per requirement #18 ("No visual cue should
implicitly modify audio"). A `MUSIC_BED_START` while a bed is already
active, a `MUSIC_DUCK`/`MUSIC_LIFT`/`MUSIC_BED_END` while none is active,
cues out of nondecreasing timestamp order, two music cues sharing the
exact same timestamp, or a music cue timestamp beyond the timeline's own
`total_duration_ms` are all rejected explicitly
(`InvalidMusicCueSequenceError`/`AudioMixPlanningError`) -- never
silently reordered, merged, or clamped.

Unterminated bed policy (requirement #4, chosen and documented per that
requirement's own preferred default): a `MUSIC_BED_START` with no
matching `MUSIC_BED_END` implicitly closes at `total_duration_ms` --
never rejected outright, since the vast majority of authored timelines
simply let the music bed run to the end of the program without an
explicit closing cue.

Gain-ramp fit (requirement #6): every produced region must be at least
`music_gain_ramp_ms` long, since app/video_encoder/encoder.py crossfades
(`acrossfade`) every adjacent region pair over exactly that duration --
a region shorter than the ramp itself cannot be crossfaded into AND out
of without overlapping ramps, so this is rejected explicitly
(`InvalidMusicCueSequenceError`) rather than silently shortening the
ramp or overlapping it.

SFX events (requirement #9): each `SFX_TRIGGER` cue's own `reference`
field is already the stable SFX identifier this phase needs (Phase 27's
own TimelineBuilder already populates it from
`RenderedVoiceTake.sfx_opportunity`) -- no new TimelineCue field was
necessary. Resolved via `AudioAssetBindings.sfx_by_id[reference]`; an
unresolvable or missing reference is `UnknownSFXReferenceError`, never a
fuzzy match or a silently-skipped event.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from app.models.common import MotilyModel, MusicState, non_blank
from app.models.timeline import TimelineCue, TimelineCueType
from app.video_encoder.errors import (
    AudioMixPlanningError,
    InvalidMusicCueSequenceError,
    UnknownSFXReferenceError,
)

if TYPE_CHECKING:
    from app.video_encoder.models import AudioAssetBindings

_MUSIC_CUE_TYPES = frozenset(
    {
        TimelineCueType.MUSIC_BED_START,
        TimelineCueType.MUSIC_DUCK,
        TimelineCueType.MUSIC_LIFT,
        TimelineCueType.MUSIC_BED_END,
    }
)
_STATE_BY_START_CUE_TYPE = {
    TimelineCueType.MUSIC_DUCK: MusicState.DUCK,
    TimelineCueType.MUSIC_LIFT: MusicState.LIFT,
}


class MusicRegionPlan(MotilyModel):
    """One contiguous, constant-gain span of the music bed -- half-open
    `[start_ms, end_ms)`, never overlapping a sibling region."""

    start_ms: int
    end_ms: int
    gain_db: float
    state: MusicState

    @model_validator(mode="after")
    def _check_invariants(self) -> "MusicRegionPlan":
        if self.start_ms < 0:
            raise ValueError(f"start_ms must be >= 0; got {self.start_ms}")
        if self.end_ms <= self.start_ms:
            raise ValueError(
                f"end_ms ({self.end_ms}) must be greater than start_ms ({self.start_ms})"
            )
        return self


class SfxEventPlan(MotilyModel):
    """One independent, one-shot SFX playback -- starts exactly at
    `timestamp_ms`, trimmed at the timeline's own end by the command
    builder, never quantized to a frame boundary."""

    timestamp_ms: int
    sfx_id: str
    file_path: Path

    @model_validator(mode="after")
    def _check_invariants(self) -> "SfxEventPlan":
        if self.timestamp_ms < 0:
            raise ValueError(f"timestamp_ms must be >= 0; got {self.timestamp_ms}")
        non_blank(self.sfx_id, "sfx_id")
        non_blank(str(self.file_path), "file_path")
        return self


class AudioMixPlan(MotilyModel):
    """The complete, deterministic output of `build_audio_mix_plan` --
    everything app/video_encoder/encoder.py needs to build the real
    filter graph, with every timing/gain decision already made."""

    music_regions: list[MusicRegionPlan] = Field(default_factory=list)
    sfx_events: list[SfxEventPlan] = Field(default_factory=list)

    @property
    def has_music(self) -> bool:
        return len(self.music_regions) > 0


def build_audio_mix_plan(
    cues: list[TimelineCue],
    bindings: "AudioAssetBindings",
    total_duration_ms: int,
    settings,
) -> AudioMixPlan:
    """Pure function: no filesystem access, no subprocess, no randomness.
    Asset existence/format/readability is validated separately by
    VideoEncoder, AFTER this plan is built -- this function only
    interprets cue timing/sequencing and resolves SFX ids against the
    bindings dict (a pure in-memory lookup)."""
    music_regions = _plan_music_regions(cues, total_duration_ms, settings)
    sfx_events = _plan_sfx_events(cues, bindings, total_duration_ms)
    return AudioMixPlan(music_regions=music_regions, sfx_events=sfx_events)


def _plan_music_regions(
    cues: list[TimelineCue], total_duration_ms: int, settings
) -> list[MusicRegionPlan]:
    gain_by_state = {
        MusicState.BED: settings.music_bed_gain_db,
        MusicState.DUCK: settings.music_duck_gain_db,
        MusicState.LIFT: settings.music_lift_gain_db,
    }
    music_cues = [cue for cue in cues if cue.cue_type in _MUSIC_CUE_TYPES]

    regions: list[MusicRegionPlan] = []
    active = False
    region_start_ms: int | None = None
    region_state: MusicState | None = None
    prev_ts: int | None = None

    for cue in music_cues:
        ts = cue.timestamp_ms
        if ts > total_duration_ms:
            raise AudioMixPlanningError(
                f"{cue.cue_type.value} cue at {ts}ms is beyond the timeline's own "
                f"total_duration_ms ({total_duration_ms}ms)"
            )
        if prev_ts is not None:
            if ts < prev_ts:
                raise InvalidMusicCueSequenceError(
                    f"Music cues are not in nondecreasing timestamp order: "
                    f"{prev_ts}ms then {ts}ms"
                )
            if ts == prev_ts:
                raise InvalidMusicCueSequenceError(
                    f"Duplicate or contradictory music cues at the same timestamp "
                    f"({ts}ms)"
                )

        if cue.cue_type is TimelineCueType.MUSIC_BED_START:
            if active:
                raise InvalidMusicCueSequenceError(
                    f"MUSIC_BED_START at {ts}ms while a music bed is already active"
                )
            active = True
            region_start_ms = ts
            region_state = MusicState.BED
        elif cue.cue_type in (TimelineCueType.MUSIC_DUCK, TimelineCueType.MUSIC_LIFT):
            if not active:
                raise InvalidMusicCueSequenceError(
                    f"{cue.cue_type.value} at {ts}ms with no active music bed"
                )
            regions.append(
                MusicRegionPlan(
                    start_ms=region_start_ms, end_ms=ts, gain_db=gain_by_state[region_state],
                    state=region_state,
                )
            )
            region_start_ms = ts
            region_state = _STATE_BY_START_CUE_TYPE[cue.cue_type]
        else:  # MUSIC_BED_END
            if not active:
                raise InvalidMusicCueSequenceError(
                    f"MUSIC_BED_END at {ts}ms with no active music bed"
                )
            regions.append(
                MusicRegionPlan(
                    start_ms=region_start_ms, end_ms=ts, gain_db=gain_by_state[region_state],
                    state=region_state,
                )
            )
            active = False
            region_start_ms = None
            region_state = None

        prev_ts = ts

    if active:
        # Requirement #4's chosen policy: an unterminated bed implicitly
        # closes at the timeline's own end, never rejected.
        regions.append(
            MusicRegionPlan(
                start_ms=region_start_ms, end_ms=total_duration_ms,
                gain_db=gain_by_state[region_state], state=region_state,
            )
        )

    if len(regions) > 1:
        for region in regions:
            if region.end_ms - region.start_ms < settings.music_gain_ramp_ms:
                raise InvalidMusicCueSequenceError(
                    f"Music region {region.start_ms}-{region.end_ms}ms is shorter than "
                    f"music_gain_ramp_ms ({settings.music_gain_ramp_ms}ms) -- these cues "
                    f"are too closely spaced to crossfade cleanly"
                )

    return regions


def count_music_cues(cues: list[TimelineCue]) -> int:
    """How many of `cues` are MUSIC_* (BED_START/DUCK/LIFT/BED_END,
    combined) -- used only for VideoEncodeResult.music_cue_count, a
    reporting figure, never consulted for planning itself."""
    return sum(1 for cue in cues if cue.cue_type in _MUSIC_CUE_TYPES)


def _plan_sfx_events(
    cues: list[TimelineCue], bindings: "AudioAssetBindings", total_duration_ms: int
) -> list[SfxEventPlan]:
    events: list[SfxEventPlan] = []
    for cue in cues:
        if cue.cue_type is not TimelineCueType.SFX_TRIGGER:
            continue
        ts = cue.timestamp_ms
        if ts > total_duration_ms:
            raise AudioMixPlanningError(
                f"SFX_TRIGGER cue at {ts}ms is beyond the timeline's own "
                f"total_duration_ms ({total_duration_ms}ms)"
            )
        sfx_id = cue.reference
        if not sfx_id:
            raise UnknownSFXReferenceError(
                f"SFX_TRIGGER cue at {ts}ms carries no reference to resolve as an SFX id"
            )
        if sfx_id not in bindings.sfx_by_id:
            raise UnknownSFXReferenceError(
                f"SFX_TRIGGER cue at {ts}ms references unknown SFX id {sfx_id!r} -- not "
                f"present in AudioAssetBindings.sfx_by_id"
            )
        events.append(SfxEventPlan(timestamp_ms=ts, sfx_id=sfx_id, file_path=bindings.sfx_by_id[sfx_id]))
    return events
