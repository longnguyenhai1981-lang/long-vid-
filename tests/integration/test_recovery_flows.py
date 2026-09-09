"""Phase 12: recovery-path integration tests (sections 9-14).

Each test drives a real backward review action, then proves the project is
genuinely recoverable from there: prior artifacts survive, references stay
coherent, nothing downstream auto-runs, and re-running the appropriate
engine produces a new current artifact without corrupting history.
"""

from __future__ import annotations

from app.engines.feasibility.engine import FeasibilityEngine
from app.engines.feasibility.models import FeasibilityEngineInput
from app.engines.idea.engine import IdeaEngine
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaEngineInput
from app.engines.narrative.engine import NarrativeEngine
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativeEngineInput
from app.engines.research_r1.engine import R1ResearchEngine
from app.engines.research_r1.models import R1ResearchInput
from app.engines.script.engine import ScriptEngine
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE, ScriptEngineInput
from app.engines.script_verification.engine import ScriptVerificationEngine
from app.engines.script_verification.models import (
    SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
    ScriptVerificationInput,
)
from app.llm.fake import FakeLLMProvider
from app.models.common import GateStatus, ProjectState
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.research.fake import FakeResearchRetriever
from app.review.service import (
    accept_script_verification,
    decide_feasibility,
    revise_final_script,
    revise_idea,
    revise_narrative,
    send_narrative_back_to_research,
    send_script_back_to_research,
    send_script_for_rewrite,
)
from app.storage.artifacts import get_artifact
from app.storage.projects import get_project
from tests.integration import pipeline_helpers as fx


# ---------------------------------------------------------------------------
# Section 9: IDEA_REVIEW -> revise_idea -> IDEA_DISCOVERY -> rerun IdeaEngine
# ---------------------------------------------------------------------------


def test_idea_revise_then_rerun_produces_new_current_idea(
    engine, global_config, llm_settings, project_at_idea_review
):
    project_id = project_at_idea_review.project_id
    original_idea = project_at_idea_review.idea

    revise_idea(engine, project_id, feedback="Central question is too vague")
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY

    provider = FakeLLMProvider([fx.response(fx.idea_candidate_json_variant())])
    result = IdeaEngine(engine, provider, global_config, llm_settings).run(
        IdeaEngineInput(project_id=project_id, discovery_mode="OPEN")
    )

    assert result.idea.id != original_idea.id
    assert get_project(engine, project_id).state == ProjectState.IDEA_REVIEW

    current = get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
    assert current.id == result.idea.id
    assert get_project(engine, project_id).idea_candidate_id == result.idea.id

    # No downstream engine auto-ran: still no R0 reference.
    assert get_project(engine, project_id).research_r0_id is None


# ---------------------------------------------------------------------------
# Section 10: FEASIBILITY REFRAME -> decide_feasibility(REFRAME) -> IDEA_DISCOVERY
# ---------------------------------------------------------------------------


def test_feasibility_reframe_preserves_prior_artifacts_and_references(
    engine, global_config, llm_settings, project_at_feasibility
):
    project_id = project_at_feasibility.project_id

    provider = FakeLLMProvider([fx.response(fx.feasibility_report_json(overall="REFRAME"))])
    result = FeasibilityEngine(engine, provider, global_config, llm_settings).run(
        FeasibilityEngineInput(project_id=project_id)
    )
    assert result.feasibility.status is GateStatus.REFRAME

    decide_feasibility(engine, project_id, GateStatus.REFRAME, feedback="Sharpen the central question")

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_DISCOVERY
    # References are never cleared on REFRAME (Phase 6 contract).
    assert project.idea_candidate_id == project_at_feasibility.idea.id
    assert project.research_r0_id == project_at_feasibility.research_r0.id
    assert project.feasibility_id == result.feasibility.id

    # Old artifacts remain fetchable.
    still_idea = get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
    assert still_idea.id == project_at_feasibility.idea.id


# ---------------------------------------------------------------------------
# Section 11: NARRATIVE_REVIEW -> revise_narrative -> NARRATIVE -> rerun NarrativeEngine
# ---------------------------------------------------------------------------


def test_narrative_revise_then_rerun_replaces_current_plan(
    engine, global_config, llm_settings, project_at_narrative_review
):
    project_id = project_at_narrative_review.project_id
    original_plan = project_at_narrative_review.narrative_plan

    revise_narrative(engine, project_id, feedback="Ladder feels rushed")
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE

    provider = FakeLLMProvider([fx.response(fx.narrative_plan_json())])
    result = NarrativeEngine(engine, provider, global_config, llm_settings).run(
        NarrativeEngineInput(project_id=project_id)
    )

    # save_artifact upserts by (project_id, artifact_type) -- a new id every
    # generation, even with identical content, so "replaced" is provable by id.
    assert result.narrative.id != original_plan.id
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE_REVIEW

    current = get_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
    assert current.id == result.narrative.id
    assert get_project(engine, project_id).narrative_plan_id == result.narrative.id


# ---------------------------------------------------------------------------
# Section 12: backward route to R1_RESEARCH
# ---------------------------------------------------------------------------


def test_narrative_review_back_to_research_is_recoverable(
    engine, global_config, llm_settings, project_at_narrative_review
):
    project_id = project_at_narrative_review.project_id

    send_narrative_back_to_research(engine, project_id, feedback="Claim needs stronger sourcing")
    assert get_project(engine, project_id).state == ProjectState.R1_RESEARCH

    # Prior artifacts remain, including the one now "orphaned" by the
    # backward move (NarrativePlan).
    assert get_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan) is not None

    # The project is genuinely recoverable: R1ResearchEngine runs again cleanly.
    provider = FakeLLMProvider([fx.response(fx.research_package_json())])
    retriever = FakeResearchRetriever(fx.r1_search_responses())
    R1ResearchEngine(engine, provider, retriever, global_config, llm_settings).run(
        R1ResearchInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE


def test_script_verification_back_to_research_preserves_script_plan(
    engine, project_at_script_verification
):
    project_id = project_at_script_verification.project_id
    script_plan = project_at_script_verification.script_plan

    send_script_back_to_research(engine, project_id, feedback="Underlying claim needs deeper research")
    assert get_project(engine, project_id).state == ProjectState.R1_RESEARCH

    # The script written against the (now possibly stale) research is still
    # on record -- backward recovery never deletes history.
    still_script = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert still_script.id == script_plan.id


# ---------------------------------------------------------------------------
# Section 13: SCRIPT_VERIFICATION -> send_script_for_rewrite -> SCRIPT -> rerun ScriptEngine
# ---------------------------------------------------------------------------


def test_script_rewrite_recovery_replaces_plan_and_requires_reverification(
    engine, global_config, llm_settings, project_at_script_verification
):
    project_id = project_at_script_verification.project_id
    original_script = project_at_script_verification.script_plan

    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="PASS"))])
    ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
        ScriptVerificationInput(project_id=project_id)
    )
    report_a = get_artifact(
        engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport
    )
    assert report_a.status.value == "PASS"

    send_script_for_rewrite(engine, project_id, feedback="Tighten the reveal beat")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT

    # Old report A is untouched while the script is being rewritten.
    assert (
        get_artifact(engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport)
        == report_a
    )

    provider = FakeLLMProvider([fx.response(fx.script_plan_json(duration=555))])
    result = ScriptEngine(engine, provider, global_config, llm_settings).run(
        ScriptEngineInput(project_id=project_id)
    )
    assert result.script.id != original_script.id
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION

    stored_script = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert stored_script.id == result.script.id
    assert stored_script.estimated_duration_seconds == 555

    # Verification has NOT re-run yet -- report A is still what's stored.
    assert (
        get_artifact(engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport)
        == report_a
    )

    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="REFRAME"))])
    ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
        ScriptVerificationInput(project_id=project_id)
    )
    report_b = get_artifact(
        engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport
    )
    assert report_b.status.value == "REFRAME"
    assert report_b != report_a


# ---------------------------------------------------------------------------
# Section 14: SCRIPT_REVIEW -> revise_final_script -> SCRIPT -> full re-verify -> SCRIPT_REVIEW
# ---------------------------------------------------------------------------


def test_final_script_revise_requires_full_reverification(
    engine, global_config, llm_settings, project_at_script_review
):
    project_id = project_at_script_review.project_id
    original_script = project_at_script_review.script_plan

    revise_final_script(engine, project_id, feedback="Tighten the ending")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT

    provider = FakeLLMProvider([fx.response(fx.script_plan_json(duration=490))])
    script_result = ScriptEngine(engine, provider, global_config, llm_settings).run(
        ScriptEngineInput(project_id=project_id)
    )
    assert script_result.script.id != original_script.id
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION

    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="PASS"))])
    ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
        ScriptVerificationInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION

    accept_script_verification(engine, project_id, feedback="Re-verified cleanly")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_REVIEW

    # The script actually changed -- this proves the edit really had to pass
    # through ScriptEngine + ScriptVerificationEngine again, not a shortcut.
    current_script = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert current_script.id == script_result.script.id
    assert current_script.estimated_duration_seconds == 490
