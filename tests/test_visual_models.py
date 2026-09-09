from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.common import ComplexityClass, VisualFunction, VisualLevel, VisualMediaType, VisualTiState
from app.models.visual import VisualBeat, VisualPlan


def _beat(beat_id="B001", script_line_ids=("L001",), media_type="ASSET_REUSE", evidence_source_ids=None) -> VisualBeat:
    return VisualBeat(
        beat_id=beat_id,
        script_line_ids=list(script_line_ids),
        narrative_node="Q0",
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type=media_type,
        complexity="C0",
        concept="An establishing shot of the bridge.",
        primary_focus="The bridge silhouette",
        evidence_source_ids=evidence_source_ids or [],
    )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_visual_level_has_exactly_three_values():
    assert {member.value for member in VisualLevel} == {"L1_ESTABLISH", "L2_ACTION", "L3_RELATIONSHIP"}


def test_visual_function_has_exactly_five_values():
    assert {member.value for member in VisualFunction} == {
        "STORY", "EVIDENCE", "MECHANISM", "METAPHOR", "EMPHASIS",
    }


def test_visual_media_type_has_exactly_eight_values():
    assert {member.value for member in VisualMediaType} == {
        "ASSET_REUSE", "TI_STATE", "DIAGRAM", "GENERATED_STILL",
        "LIMITED_MOTION", "EVIDENCE_MEDIA", "AI_HERO_VIDEO", "COMPOSITION",
    }


def test_complexity_class_has_exactly_four_values():
    assert {member.value for member in ComplexityClass} == {"C0", "C1", "C2", "C3"}


def test_visual_ti_state_has_exactly_nine_values():
    assert {member.value for member in VisualTiState} == {
        "NEUTRAL", "CURIOUS", "SKEPTICAL", "CONFUSED", "SURPRISED", "PANIC", "SMUG", "DEADPAN", "EXCITED",
    }


# ---------------------------------------------------------------------------
# VisualBeat
# ---------------------------------------------------------------------------


def test_visual_beat_constructs_with_required_fields():
    beat = _beat()
    assert beat.beat_id == "B001"
    assert beat.ti_state is None
    assert beat.evidence_source_ids == []
    assert beat.secondary_elements == []
    assert beat.context_elements == []


def test_visual_beat_rejects_blank_beat_id():
    with pytest.raises(ValidationError):
        _beat(beat_id="   ")


def test_visual_beat_rejects_empty_script_line_ids():
    with pytest.raises(ValidationError):
        VisualBeat(
            beat_id="B001",
            script_line_ids=[],
            narrative_node="Q0",
            visual_level="L1_ESTABLISH",
            visual_function="STORY",
            media_type="ASSET_REUSE",
            complexity="C0",
            concept="c",
            primary_focus="f",
        )


def test_visual_beat_rejects_blank_primary_focus():
    with pytest.raises(ValidationError):
        VisualBeat(
            beat_id="B001",
            script_line_ids=["L001"],
            narrative_node="Q0",
            visual_level="L1_ESTABLISH",
            visual_function="STORY",
            media_type="ASSET_REUSE",
            complexity="C0",
            concept="c",
            primary_focus="   ",
        )


def test_visual_beat_rejects_blank_concept():
    with pytest.raises(ValidationError):
        VisualBeat(
            beat_id="B001",
            script_line_ids=["L001"],
            narrative_node="Q0",
            visual_level="L1_ESTABLISH",
            visual_function="STORY",
            media_type="ASSET_REUSE",
            complexity="C0",
            concept="   ",
            primary_focus="f",
        )


def test_visual_beat_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        VisualBeat(
            beat_id="B001",
            script_line_ids=["L001"],
            narrative_node="Q0",
            visual_level="L1_ESTABLISH",
            visual_function="STORY",
            media_type="ASSET_REUSE",
            complexity="C0",
            concept="c",
            primary_focus="f",
            unexpected_field="nope",
        )


def test_visual_beat_accepts_evidence_media_with_source_ids():
    beat = _beat(media_type="EVIDENCE_MEDIA", evidence_source_ids=["S001"])
    assert beat.media_type.value == "EVIDENCE_MEDIA"
    assert beat.evidence_source_ids == ["S001"]


def test_visual_beat_accepts_optional_ti_state_and_motion_intent():
    beat = VisualBeat(
        beat_id="B001",
        script_line_ids=["L001"],
        narrative_node="Q0",
        visual_level="L2_ACTION",
        visual_function="EMPHASIS",
        media_type="TI_STATE",
        complexity="C0",
        concept="Tí reacts",
        primary_focus="Tí's face",
        ti_state="SURPRISED",
        motion_intent="Tí eyebrow change",
    )
    assert beat.ti_state.value == "SURPRISED"
    assert beat.motion_intent == "Tí eyebrow change"


# ---------------------------------------------------------------------------
# VisualPlan
# ---------------------------------------------------------------------------


def test_visual_plan_constructs_with_beats():
    plan = VisualPlan(script_plan_id=uuid4(), voice_plan_id=uuid4(), beats=[_beat()])
    assert plan.id is not None
    assert len(plan.beats) == 1
    assert plan.reusable_assets == []


def test_visual_plan_rejects_empty_beats():
    with pytest.raises(ValidationError):
        VisualPlan(script_plan_id=uuid4(), voice_plan_id=uuid4(), beats=[])


def test_visual_plan_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        VisualPlan(script_plan_id=uuid4(), voice_plan_id=uuid4(), beats=[_beat()], extra_field="nope")
