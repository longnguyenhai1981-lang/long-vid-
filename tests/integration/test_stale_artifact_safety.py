"""Phase 12: stale-artifact safety (sections 25-27).

These tests probe whether a stale artifact -- one generated for a *previous*
version of something it depends on -- can be used through the public review
surface to bypass a gate that should require a fresh one.
"""

from __future__ import annotations

import pytest

from app.engines.narrative.engine import NarrativeEngine
from app.engines.narrative.models import NarrativeEngineInput
from app.engines.packaging_p0.engine import PackagingP0Engine
from app.engines.packaging_p0.models import PackagingP0Input
from app.engines.script.engine import ScriptEngine
from app.engines.script.models import ScriptEngineInput
from app.engines.script_verification.engine import ScriptVerificationEngine
from app.engines.script_verification.models import (
    SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
    ScriptVerificationInput,
)
from app.llm.fake import FakeLLMProvider
from app.models.common import ProjectState
from app.models.script import ScriptVerificationReport
from app.review.errors import (
    ScriptVerificationNotPassedError,
    StalePackagingPrototypeError,
    StaleScriptVerificationError,
)
from app.review.service import (
    accept_script_verification,
    approve_narrative,
    approve_packaging_p0,
    revise_packaging_p0,
    send_script_for_rewrite,
)
from app.storage.artifacts import get_artifact
from app.storage.projects import get_project
from tests.integration import pipeline_helpers as fx


# ---------------------------------------------------------------------------
# Section 26: stale PASS verification must not validate a rewritten script
# ---------------------------------------------------------------------------


def test_stale_pass_verification_cannot_wave_through_a_rewritten_script(
    engine, global_config, llm_settings, project_at_script_verification
):
    project_id = project_at_script_verification.project_id
    script_a = project_at_script_verification.script_plan

    # 1. Script A verified PASS.
    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="PASS"))])
    ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
        ScriptVerificationInput(project_id=project_id)
    )
    report_a = get_artifact(
        engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport
    )
    assert report_a.status.value == "PASS"

    # 2. project returns to SCRIPT.
    send_script_for_rewrite(engine, project_id, feedback="Rework the reveal beat")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT

    # 3. Script B generated.
    provider = FakeLLMProvider([fx.response(fx.script_plan_json(duration=600))])
    script_b_result = ScriptEngine(engine, provider, global_config, llm_settings).run(
        ScriptEngineInput(project_id=project_id)
    )
    assert script_b_result.script.id != script_a.id
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION

    # The stored "current" report is STILL report A -- generated for Script A,
    # not Script B. It has not been touched since step 1.
    still_a = get_artifact(
        engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport
    )
    assert still_a == report_a

    # 4. Before verifying Script B, attempt any path toward final approval.
    # Expected: workflow state prevents using the stale PASS report to reach
    # MVP_COMPLETE (Phase 12 section 26) -- accept_script_verification must
    # refuse to accept Script B on Script A's verification.
    with pytest.raises(StaleScriptVerificationError):
        accept_script_verification(engine, project_id, feedback="Looks fine")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION

    # Now verify Script B for real, with a non-PASS report.
    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="REFRAME"))])
    ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
        ScriptVerificationInput(project_id=project_id)
    )

    # Final approval must fail -- Script B's own verification is not PASS
    # (now a fresh, correctly-scoped rejection, not a stale one).
    with pytest.raises(ScriptVerificationNotPassedError):
        accept_script_verification(engine, project_id)
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


# ---------------------------------------------------------------------------
# Section 27: stale packaging must not bypass a fresh Packaging P0 gate
# ---------------------------------------------------------------------------


def test_stale_packaging_reference_survives_a_narrative_revision_round_trip(
    engine, global_config, llm_settings, project_at_packaging_p0
):
    project_id = project_at_packaging_p0.project_id

    provider = FakeLLMProvider([fx.response(fx.packaging_prototype_json())])
    old_result = PackagingP0Engine(engine, provider, global_config, llm_settings).run(
        PackagingP0Input(project_id=project_id)
    )
    old_packaging_id = old_result.packaging.id

    # PACKAGING_P0 -> NARRATIVE (framing needs work) -> rerun NarrativeEngine
    # -> NARRATIVE_REVIEW -> approve_narrative -> PACKAGING_P0. The OLD
    # PackagingPrototype reference is never cleared by this round trip.
    revise_packaging_p0(engine, project_id, feedback="Promise overstates the reveal")
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE

    provider = FakeLLMProvider([fx.response(fx.narrative_plan_json())])
    NarrativeEngine(engine, provider, global_config, llm_settings).run(
        NarrativeEngineInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE_REVIEW

    approve_narrative(engine, project_id, feedback="Better arc now")
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0
    assert get_project(engine, project_id).packaging_prototype_id == old_packaging_id

    # Expected: the stale PackagingPrototype (generated for the OLD
    # narrative) must not be approvable forward to SCRIPT until packaging is
    # regenerated against the CURRENT narrative (Phase 12 section 27).
    with pytest.raises(StalePackagingPrototypeError):
        approve_packaging_p0(engine, project_id, feedback="Ship it")
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0

    # And the project is genuinely recoverable: regenerating packaging
    # against the current narrative clears the staleness and approval
    # succeeds normally.
    provider = FakeLLMProvider([fx.response(fx.packaging_prototype_json())])
    PackagingP0Engine(engine, provider, global_config, llm_settings).run(
        PackagingP0Input(project_id=project_id)
    )
    approve_packaging_p0(engine, project_id, feedback="Ship it, now fresh")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT
