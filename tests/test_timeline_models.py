"""Phase 27 focused tests: TimelineManifest model/contract validation
(app/models/timeline.py).

Pure pydantic-level tests -- no builder, no filesystem I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.timeline import (
    TimelineAudioRef,
    TimelineCue,
    TimelineCueType,
    TimelineManifest,
    TimelineSegment,
    TimelineTransitionType,
    TimelineVisualRef,
    TimelineVisualSourceStatus,
)


def _audio_ref(**overrides) -> TimelineAudioRef:
    fields = dict(chunk_id="C001", render_job_id="C001_T1", file_path="C001_T1.wav", duration_ms=2000)
    fields.update(overrides)
    return TimelineAudioRef(**fields)


def _rendered_visual_ref(**overrides) -> TimelineVisualRef:
    fields = dict(
        visual_beat_id="V1", media_type="GENERATED_STILL",
        status=TimelineVisualSourceStatus.RENDERED, file_path="V1_R1.png",
    )
    fields.update(overrides)
    return TimelineVisualRef(**fields)


def _requirement_visual_ref(**overrides) -> TimelineVisualRef:
    fields = dict(
        visual_beat_id="V2", media_type="ASSET_REUSE",
        status=TimelineVisualSourceStatus.REQUIREMENT, reference="ti_hero_shot",
    )
    fields.update(overrides)
    return TimelineVisualRef(**fields)


def _segment(**overrides) -> TimelineSegment:
    fields = dict(
        segment_id="S1", script_line_ids=["L001"], start_ms=0, end_ms=2000, duration_ms=2000,
        narration=[_audio_ref()], visual=_rendered_visual_ref(),
        transition_in=TimelineTransitionType.HOLD, transition_out=TimelineTransitionType.CUT,
        source_transition_in="NONE", source_transition_out="CUT", music_state="BED",
    )
    fields.update(overrides)
    return TimelineSegment(**fields)


def _manifest(**overrides) -> TimelineManifest:
    fields = dict(
        project_id=uuid4(), script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
        voice_render_manifest_id=uuid4(), visual_render_manifest_id=uuid4(), assembly_plan_id=uuid4(),
        total_duration_ms=2000, segments=[_segment()], created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return TimelineManifest(**fields)


# ---------------------------------------------------------------------------
# TimelineAudioRef
# ---------------------------------------------------------------------------


def test_audio_ref_zero_duration_rejected():
    with pytest.raises(ValidationError):
        _audio_ref(duration_ms=0)


def test_audio_ref_negative_duration_rejected():
    with pytest.raises(ValidationError):
        _audio_ref(duration_ms=-500)


def test_audio_ref_blank_chunk_id_rejected():
    with pytest.raises(ValidationError):
        _audio_ref(chunk_id="   ")


def test_audio_ref_valid_accepted():
    ref = _audio_ref()
    assert ref.duration_ms == 2000


# ---------------------------------------------------------------------------
# TimelineVisualRef
# ---------------------------------------------------------------------------


def test_rendered_status_requires_file_path():
    with pytest.raises(ValidationError):
        TimelineVisualRef(visual_beat_id="V1", media_type="GENERATED_STILL", status="RENDERED")


def test_rendered_status_forbids_reference():
    with pytest.raises(ValidationError):
        TimelineVisualRef(
            visual_beat_id="V1", media_type="GENERATED_STILL", status="RENDERED",
            file_path="V1_R1.png", reference="oops",
        )


def test_requirement_status_forbids_file_path():
    with pytest.raises(ValidationError):
        TimelineVisualRef(
            visual_beat_id="V2", media_type="ASSET_REUSE", status="REQUIREMENT",
            file_path="oops.png",
        )


def test_requirement_status_accepts_no_file_path():
    ref = _requirement_visual_ref()
    assert ref.file_path is None
    assert ref.reference == "ti_hero_shot"


def test_visual_ref_negative_width_rejected():
    with pytest.raises(ValidationError):
        _rendered_visual_ref(width=-1)


# ---------------------------------------------------------------------------
# TimelineSegment: timebase invariants
# ---------------------------------------------------------------------------


def test_negative_start_ms_rejected():
    with pytest.raises(ValidationError):
        _segment(start_ms=-1, end_ms=100, duration_ms=101)


def test_zero_duration_rejected():
    with pytest.raises(ValidationError):
        _segment(start_ms=0, end_ms=0, duration_ms=0)


def test_negative_duration_rejected():
    with pytest.raises(ValidationError):
        _segment(start_ms=100, end_ms=50, duration_ms=-50)


def test_duration_mismatch_rejected():
    with pytest.raises(ValidationError):
        _segment(start_ms=0, end_ms=2000, duration_ms=1000)


def test_valid_segment_accepted():
    segment = _segment()
    assert segment.duration_ms == 2000


def test_segment_requires_at_least_one_narration_ref():
    with pytest.raises(ValidationError):
        _segment(narration=[])


def test_segment_blank_id_rejected():
    with pytest.raises(ValidationError):
        _segment(segment_id="   ")


def test_timeline_transition_type_has_exactly_three_values():
    assert {member.value for member in TimelineTransitionType} == {"CUT", "HOLD", "CROSSFADE"}


# ---------------------------------------------------------------------------
# TimelineCue
# ---------------------------------------------------------------------------


def test_cue_negative_timestamp_rejected():
    with pytest.raises(ValidationError):
        TimelineCue(timestamp_ms=-1, cue_type="SFX_TRIGGER")


def test_cue_valid_accepted():
    cue = TimelineCue(timestamp_ms=0, cue_type="MUSIC_BED_START")
    assert cue.cue_type is TimelineCueType.MUSIC_BED_START


def test_cue_type_has_exactly_seven_values():
    assert {member.value for member in TimelineCueType} == {
        "MUSIC_BED_START", "MUSIC_DUCK", "MUSIC_LIFT", "MUSIC_BED_END",
        "SFX_TRIGGER", "CUT", "STATIC_HOLD",
    }


# ---------------------------------------------------------------------------
# TimelineManifest
# ---------------------------------------------------------------------------


def test_manifest_requires_at_least_one_segment():
    with pytest.raises(ValidationError):
        _manifest(segments=[])


def test_manifest_total_duration_must_match_last_segment_end():
    with pytest.raises(ValidationError):
        _manifest(total_duration_ms=9999)


def test_manifest_total_duration_zero_rejected():
    with pytest.raises(ValidationError):
        _manifest(total_duration_ms=0, segments=[_segment(start_ms=0, end_ms=0, duration_ms=0)])


def test_manifest_naive_created_at_rejected():
    with pytest.raises(ValidationError):
        _manifest(created_at=datetime.now())  # no tzinfo


def test_manifest_valid_accepted():
    manifest = _manifest()
    assert manifest.total_duration_ms == 2000
    assert len(manifest.segments) == 1
    assert manifest.cues == []


def test_manifest_two_segment_cumulative_total():
    seg1 = _segment(segment_id="S1", start_ms=0, end_ms=2000, duration_ms=2000)
    seg2 = _segment(
        segment_id="S2", start_ms=2000, end_ms=3500, duration_ms=1500,
        narration=[_audio_ref(chunk_id="C002", render_job_id="C002_T1", file_path="C002_T1.wav", duration_ms=1500)],
        visual=_requirement_visual_ref(),
    )
    manifest = _manifest(total_duration_ms=3500, segments=[seg1, seg2])
    assert manifest.total_duration_ms == 3500
