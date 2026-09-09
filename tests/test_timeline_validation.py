"""Phase 27 focused tests: validate_timeline_manifest
(app/renderers/timeline/validation.py).

Pure-function, I/O-free tests -- exercises the defense-in-depth checks
TimelineBuilder runs on a freshly built TimelineManifest before
persisting it, independent of the full builder pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.models.assembly import AssemblySegment
from app.models.common import MusicState, TransitionIntent
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.timeline import (
    TimelineAudioRef,
    TimelineCue,
    TimelineManifest,
    TimelineSegment,
    TimelineTransitionType,
    TimelineVisualRef,
    TimelineVisualSourceStatus,
)
from app.models.visual import VisualBeat, VisualPlan
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.timeline.validation import validate_timeline_manifest


def _script_plan() -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=10,
        beats=[
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM",
                lines=[ScriptLine(line_id="L001", text="one", function="INFORM")],
            )
        ],
        qa_status="PASS",
    )


def _voice_plan(script_plan_id) -> VoicePlan:
    return VoicePlan(
        script_plan_id=script_plan_id,
        chunks=[VoiceChunk(chunk_id="C001", line_ids=["L001"], voice_state="NEUTRAL", pace="NORMAL", energy="MEDIUM", take_count=1, music_state="BED")],
    )


def _visual_plan(script_plan_id, voice_plan_id) -> VisualPlan:
    return VisualPlan(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id,
        beats=[
            VisualBeat(
                beat_id="V1", script_line_ids=["L001"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="GENERATED_STILL",
                complexity="C1", concept="c", primary_focus="f",
            )
        ],
    )


def _segment(**overrides) -> TimelineSegment:
    fields = dict(
        segment_id="S1", script_line_ids=["L001"], start_ms=0, end_ms=1000, duration_ms=1000,
        narration=[TimelineAudioRef(chunk_id="C001", render_job_id="C001_T1", file_path="C001_T1.wav", duration_ms=1000)],
        visual=TimelineVisualRef(
            visual_beat_id="V1", media_type="GENERATED_STILL",
            status=TimelineVisualSourceStatus.RENDERED, file_path="V1_R1.png",
        ),
        transition_in=TimelineTransitionType.HOLD, transition_out=TimelineTransitionType.CUT,
        source_transition_in=TransitionIntent.NONE, source_transition_out=TransitionIntent.CUT,
        music_state=MusicState.BED,
    )
    fields.update(overrides)
    return TimelineSegment(**fields)


def _manifest(script_plan, voice_plan, visual_plan, segments, cues=None, total_duration_ms=None) -> TimelineManifest:
    return TimelineManifest(
        project_id=uuid4(), script_plan_id=script_plan.id, voice_plan_id=voice_plan.id,
        visual_plan_id=visual_plan.id, voice_render_manifest_id=uuid4(),
        visual_render_manifest_id=uuid4(), assembly_plan_id=uuid4(),
        total_duration_ms=total_duration_ms if total_duration_ms is not None else segments[-1].end_ms,
        segments=segments, cues=cues or [], created_at=datetime.now(timezone.utc),
    )


def test_valid_manifest_has_no_issues():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    manifest = _manifest(script_plan, voice_plan, visual_plan, [_segment()])

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert issues == []


def test_mismatched_script_plan_id_flagged():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    manifest = _manifest(script_plan, voice_plan, visual_plan, [_segment()])
    other_script_plan = _script_plan()

    issues = validate_timeline_manifest(manifest, other_script_plan, voice_plan, visual_plan)
    assert any("script_plan_id" in issue for issue in issues)


def test_duplicate_segment_id_flagged():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment(segment_id="S1", start_ms=0, end_ms=1000, duration_ms=1000)
    seg2 = _segment(segment_id="S1", start_ms=1000, end_ms=2000, duration_ms=1000)
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1, seg2])

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert any("Duplicate segment_id" in issue for issue in issues)


def test_gap_between_segments_rejected():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment(segment_id="S1", start_ms=0, end_ms=1000, duration_ms=1000)
    seg2 = _segment(segment_id="S2", start_ms=1500, end_ms=2500, duration_ms=1000)  # 500ms gap
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1, seg2], total_duration_ms=2500)

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert any("contiguous" in issue for issue in issues)


def test_overlap_between_segments_rejected():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment(segment_id="S1", start_ms=0, end_ms=1000, duration_ms=1000)
    seg2 = _segment(segment_id="S2", start_ms=500, end_ms=1500, duration_ms=1000)  # overlaps seg1
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1, seg2], total_duration_ms=1500)

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert any("contiguous" in issue for issue in issues)


def test_first_segment_not_starting_at_zero_rejected():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment(segment_id="S1", start_ms=100, end_ms=1100, duration_ms=1000)
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1], total_duration_ms=1100)

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert any("must start at 0" in issue for issue in issues)


def test_cue_timestamp_outside_duration_rejected():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment()
    cue = TimelineCue(timestamp_ms=5000, cue_type="SFX_TRIGGER")  # beyond total_duration_ms=1000
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1], cues=[cue])

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert any("outside" in issue for issue in issues)


def test_cue_timestamp_within_duration_accepted():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment()
    cue = TimelineCue(timestamp_ms=500, cue_type="SFX_TRIGGER")
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1], cues=[cue])

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert issues == []


def test_contiguous_multi_segment_timeline_accepted():
    script_plan = _script_plan()
    voice_plan = _voice_plan(script_plan.id)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    seg1 = _segment(segment_id="S1", start_ms=0, end_ms=1000, duration_ms=1000)
    seg2 = _segment(segment_id="S2", start_ms=1000, end_ms=2000, duration_ms=1000)
    manifest = _manifest(script_plan, voice_plan, visual_plan, [seg1, seg2], total_duration_ms=2000)

    issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
    assert issues == []
