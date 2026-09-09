from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.packaging_p1.validation import (
    normalize_final_packaging_plan,
    validate_final_packaging_plan,
)
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.visual import VisualBeat, VisualPlan


def _plan(**overrides) -> FinalPackagingPlan:
    kwargs = dict(
        packaging_prototype_id=uuid4(),
        script_plan_id=uuid4(),
        visual_plan_id=uuid4(),
        assembly_plan_id=uuid4(),
        title="Chiếc cầu tự xé nát chính nó",
        thumbnail_text="CHỈ VÌ GIÓ?",
        thumbnail_concept="Bridge deck twisting sharply.",
        final_promise="A sturdy bridge tore itself apart in ordinary wind.",
        expected_payoff="The real aerodynamic mechanism, not resonance.",
        viewer_expectation="An honest investigation into a real physical mechanism.",
        rationale="Sharpens P0's promise now that the reveal is locked in.",
        risk_of_misleading="LOW",
    )
    kwargs.update(overrides)
    return FinalPackagingPlan(**kwargs)


# ---------------------------------------------------------------------------
# Section 45: non-blank required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    ["title", "thumbnail_concept", "final_promise", "expected_payoff", "viewer_expectation", "rationale"],
)
def test_blank_required_field_flagged(field_name):
    plan = _plan(**{field_name: "   "})
    issues = validate_final_packaging_plan(plan)
    assert any(f"{field_name} cannot be blank" in issue for issue in issues)


def test_valid_plan_has_no_issues():
    assert validate_final_packaging_plan(_plan()) == []


# ---------------------------------------------------------------------------
# Section 46: optional thumbnail_text
# ---------------------------------------------------------------------------


def test_thumbnail_text_none_is_valid():
    assert validate_final_packaging_plan(_plan(thumbnail_text=None)) == []


def test_thumbnail_text_empty_string_is_invalid():
    issues = validate_final_packaging_plan(_plan(thumbnail_text=""))
    assert any("thumbnail_text is present but blank" in issue for issue in issues)


def test_thumbnail_text_whitespace_only_is_invalid():
    issues = validate_final_packaging_plan(_plan(thumbnail_text="   "))
    assert any("thumbnail_text is present but blank" in issue for issue in issues)


def test_thumbnail_text_real_value_is_valid():
    assert validate_final_packaging_plan(_plan(thumbnail_text="CHỈ VÌ GIÓ?")) == []


def test_multiple_blank_fields_all_reported_together():
    plan = _plan(title="", final_promise="", rationale="")
    issues = validate_final_packaging_plan(plan)
    assert len(issues) == 3


# ---------------------------------------------------------------------------
# Section 40/27: deterministic normalization
# ---------------------------------------------------------------------------


def _script_plan() -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM",
                lines=[ScriptLine(line_id="L001", text="Line.", function="INFORM")],
            )
        ],
        qa_status="PASS",
    )


def _visual_plan() -> VisualPlan:
    return VisualPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(),
        beats=[
            VisualBeat(
                beat_id="VB001", script_line_ids=["L001"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="ASSET_REUSE",
                complexity="C0", concept="A shot.", primary_focus="The subject",
            )
        ],
    )


def _assembly_plan() -> AssemblyPlan:
    return AssemblyPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
        estimated_total_duration_seconds=5.0,
        segments=[
            AssemblySegment(
                segment_id="S001", script_line_ids=["L001"], visual_beat_id="VB001",
                start_seconds=0.0, end_seconds=5.0, music_state="BED",
                transition_in="CUT", transition_out="CUT",
            )
        ],
    )


def _packaging_prototype() -> PackagingPrototype:
    return PackagingPrototype(
        promise="p", title_direction="t", thumbnail_conflict="c",
        viewer_expectation="v", risk_of_misleading="LOW",
    )


def test_normalize_overwrites_all_four_upstream_ids():
    packaging_prototype = _packaging_prototype()
    script_plan = _script_plan()
    visual_plan = _visual_plan()
    assembly_plan = _assembly_plan()

    plan = _plan()  # random ids from the fixture, deliberately wrong
    normalized = normalize_final_packaging_plan(
        plan, packaging_prototype, script_plan, visual_plan, assembly_plan
    )

    assert normalized.packaging_prototype_id == packaging_prototype.id
    assert normalized.script_plan_id == script_plan.id
    assert normalized.visual_plan_id == visual_plan.id
    assert normalized.assembly_plan_id == assembly_plan.id


def test_normalize_does_not_touch_content_fields():
    packaging_prototype = _packaging_prototype()
    script_plan = _script_plan()
    visual_plan = _visual_plan()
    assembly_plan = _assembly_plan()

    plan = _plan(title="My Real Title")
    normalized = normalize_final_packaging_plan(
        plan, packaging_prototype, script_plan, visual_plan, assembly_plan
    )

    assert normalized.title == "My Real Title"
    assert normalized.thumbnail_text == plan.thumbnail_text
    assert normalized.risk_of_misleading == plan.risk_of_misleading
