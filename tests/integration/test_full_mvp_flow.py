"""Phase 12: does one project actually move from NEW_PROJECT to MVP_COMPLETE
using only the implemented public engine/review operations, with every
required artifact persisted, every human gate respected, and every invalid
bypass rejected?

test_full_happy_path_reaches_mvp_complete is the literal, step-by-step proof
(Phase 12 section 3) -- every call is a real public operation, in the order a
human/future-orchestrator would actually issue them; no orchestrator helper
hides the sequence. The remaining tests in this file audit what the happy
path leaves behind (artifacts, approvals, ModuleRuns) and that the human
gates cannot be bypassed through the public engine/review surface (section
7) -- explicitly NOT by attacking Phase 2's topology validator, which is
intentionally permissive about raw state-graph edges (section 8).
"""

from __future__ import annotations

import pytest

from app.engines.errors import EngineStateError
from app.engines.feasibility.engine import FeasibilityEngine
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityEngineInput
from app.engines.idea.engine import IdeaEngine
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaEngineInput
from app.engines.narrative.engine import NarrativeEngine
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativeEngineInput
from app.engines.packaging_p0.engine import PackagingP0Engine
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingP0Input
from app.engines.research_r0.engine import R0ResearchEngine
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE, R0ResearchInput
from app.engines.research_r1.engine import R1ResearchEngine
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE, R1ResearchInput
from app.engines.script.engine import ScriptEngine
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE, ScriptEngineInput
from app.engines.script_verification.engine import ScriptVerificationEngine
from app.engines.script_verification.models import (
    SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
    ScriptVerificationInput,
)
from app.llm.fake import FakeLLMProvider
from app.models.common import ApprovalStatus, ModuleRunStatus, ProjectState
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.research import ResearchPackage, ResearchR0
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.research.fake import FakeResearchRetriever
from app.review.errors import ReviewStateError
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.approvals import get_latest_approval_for_stage, list_approvals_for_project
from app.storage.artifacts import get_artifact
from app.storage.module_runs import list_module_runs_for_project
from app.storage.projects import get_project, update_project_state
from app.workflow.errors import InvalidStateTransitionError
from tests.integration import pipeline_helpers as fx


def test_full_happy_path_reaches_mvp_complete(engine, global_config, llm_settings):
    from datetime import datetime, timezone

    from app.models.project import Project
    from app.storage.projects import create_project

    # 1. create Project in NEW_PROJECT
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Tacoma Narrows - Happy Path",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    project_id = project.project_id
    assert get_project(engine, project_id).state == ProjectState.NEW_PROJECT

    # 2. NEW_PROJECT -> IDEA_DISCOVERY
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY

    # 3. run IdeaEngine -> IDEA_REVIEW
    idea_provider = FakeLLMProvider([fx.response(fx.idea_candidate_json())])
    idea_result = IdeaEngine(engine, idea_provider, global_config, llm_settings).run(
        IdeaEngineInput(project_id=project_id, discovery_mode="OPEN")
    )
    assert get_project(engine, project_id).state == ProjectState.IDEA_REVIEW

    # 4. approve_idea -> R0_RESEARCH
    approve_idea(engine, project_id, feedback="Strong hook, approved")
    assert get_project(engine, project_id).state == ProjectState.R0_RESEARCH

    # 5. run R0ResearchEngine -> FEASIBILITY
    r0_provider = FakeLLMProvider([fx.response(fx.research_r0_json())])
    r0_retriever = FakeResearchRetriever(fx.r0_search_responses())
    r0_result = R0ResearchEngine(engine, r0_provider, r0_retriever, llm_settings).run(
        R0ResearchInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY

    # 6. run FeasibilityEngine -> remain FEASIBILITY
    feasibility_provider = FakeLLMProvider([fx.response(fx.feasibility_report_json())])
    feasibility_result = FeasibilityEngine(engine, feasibility_provider, global_config, llm_settings).run(
        FeasibilityEngineInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY

    # 7. decide_feasibility(PASS) -> R1_RESEARCH
    from app.models.common import GateStatus

    decide_feasibility(engine, project_id, GateStatus.PASS, feedback="Proceed to deep research")
    assert get_project(engine, project_id).state == ProjectState.R1_RESEARCH

    # 8. run R1ResearchEngine -> NARRATIVE
    r1_provider = FakeLLMProvider([fx.response(fx.research_package_json())])
    r1_retriever = FakeResearchRetriever(fx.r1_search_responses())
    r1_result = R1ResearchEngine(engine, r1_provider, r1_retriever, global_config, llm_settings).run(
        R1ResearchInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE

    # 9. run NarrativeEngine -> NARRATIVE_REVIEW
    narrative_provider = FakeLLMProvider([fx.response(fx.narrative_plan_json())])
    narrative_result = NarrativeEngine(engine, narrative_provider, global_config, llm_settings).run(
        NarrativeEngineInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE_REVIEW

    # 10. approve_narrative -> PACKAGING_P0
    approve_narrative(engine, project_id, feedback="Strong investigative arc")
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0

    # 11. run PackagingP0Engine -> remain PACKAGING_P0
    packaging_provider = FakeLLMProvider([fx.response(fx.packaging_prototype_json())])
    packaging_result = PackagingP0Engine(engine, packaging_provider, global_config, llm_settings).run(
        PackagingP0Input(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0

    # 12. approve_packaging_p0 -> SCRIPT
    approve_packaging_p0(engine, project_id, feedback="Promise is strong and honest")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT

    # 13. run ScriptEngine -> SCRIPT_VERIFICATION
    script_provider = FakeLLMProvider([fx.response(fx.script_plan_json())])
    script_result = ScriptEngine(engine, script_provider, global_config, llm_settings).run(
        ScriptEngineInput(project_id=project_id)
    )
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION

    # 14. run ScriptVerificationEngine -> remain SCRIPT_VERIFICATION
    verification_provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="PASS"))])
    verification_result = ScriptVerificationEngine(
        engine, verification_provider, global_config, llm_settings
    ).run(ScriptVerificationInput(project_id=project_id))
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION
    assert verification_result.report.status.value == "PASS"

    # 15. accept_script_verification -> SCRIPT_REVIEW
    accept_script_verification(engine, project_id, feedback="Clean verification")
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_REVIEW

    # 16. approve_final_script -> MVP_COMPLETE
    approve_final_script(engine, project_id, feedback="Ready to publish")
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE


# ---------------------------------------------------------------------------
# Section 4: happy-path artifact checks
# ---------------------------------------------------------------------------


def test_mvp_complete_artifacts_are_all_present_and_referenced(engine, project_at_mvp_complete):
    project_id = project_at_mvp_complete.project_id
    project = get_project(engine, project_id)

    idea = get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
    research_r0 = get_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)
    feasibility = get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
    research_package = get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
    narrative_plan = get_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
    packaging = get_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
    script_plan = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    verification_report = get_artifact(
        engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, ScriptVerificationReport
    )

    assert project.idea_candidate_id == idea.id
    assert project.research_r0_id == research_r0.id
    assert project.feasibility_id == feasibility.id
    assert project.research_r1_id == research_package.id
    assert project.narrative_plan_id == narrative_plan.id
    assert project.packaging_prototype_id == packaging.id
    assert project.script_plan_id == script_plan.id

    # ScriptVerificationReport has no id and no Project reference field
    # (Phase 11, by design) -- retrieval by canonical artifact type alone
    # is the only lookup path, and it must work.
    assert verification_report.status.value == "PASS"
    assert not hasattr(verification_report, "id")


# ---------------------------------------------------------------------------
# Section 5: human gate checks
# ---------------------------------------------------------------------------


def test_mvp_complete_human_approval_gates_are_recorded(engine, project_at_mvp_complete):
    project_id = project_at_mvp_complete.project_id
    approvals = list_approvals_for_project(engine, project_id)

    expected_stages = {
        ProjectState.IDEA_REVIEW,
        ProjectState.FEASIBILITY,
        ProjectState.NARRATIVE_REVIEW,
        ProjectState.PACKAGING_P0,
        ProjectState.SCRIPT_VERIFICATION,
        ProjectState.SCRIPT_REVIEW,
    }
    actual_stages = {approval.stage for approval in approvals}
    assert actual_stages == expected_stages, "no approval invented for an engine-only stage"

    for approval in approvals:
        assert approval.project_id == project_id
        assert approval.status is ApprovalStatus.APPROVED

    # Latest-stage retrieval must resolve each gate correctly.
    for stage in expected_stages:
        latest = get_latest_approval_for_stage(engine, project_id, stage)
        assert latest is not None
        assert latest.stage is stage
        assert latest.status is ApprovalStatus.APPROVED

    # Engine-only stages (R0_RESEARCH, R1_RESEARCH, NARRATIVE, SCRIPT,
    # PACKAGING_P0-precursor NARRATIVE, etc.) never got an invented approval.
    assert get_latest_approval_for_stage(engine, project_id, ProjectState.R0_RESEARCH) is None
    assert get_latest_approval_for_stage(engine, project_id, ProjectState.R1_RESEARCH) is None
    assert get_latest_approval_for_stage(engine, project_id, ProjectState.NARRATIVE) is None
    assert get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT) is None


# ---------------------------------------------------------------------------
# Section 6: ModuleRun checks
# ---------------------------------------------------------------------------


def test_mvp_complete_module_runs_are_recorded(engine, project_at_mvp_complete):
    project_id = project_at_mvp_complete.project_id
    runs = list_module_runs_for_project(engine, project_id)

    expected_modules = {
        "idea_engine",
        "research_r0_engine",
        "feasibility_engine",
        "research_r1_engine",
        "narrative_engine",
        "packaging_p0_engine",
        "script_engine",
        "script_verification_engine",
    }
    actual_modules = {run.module for run in runs}
    assert actual_modules == expected_modules

    by_module = {run.module: run for run in runs}
    for module, run in by_module.items():
        assert run.project_id == project_id
        assert run.status is ModuleRunStatus.SUCCESS
        assert run.completed_at is not None
        assert run.module_version == "0.1"

    # output_id semantics: every engine that produces an id-bearing artifact
    # records it; ScriptVerificationEngine legitimately records None because
    # ScriptVerificationReport has no id (Phase 11, by design -- not "fixed"
    # here).
    for module in expected_modules - {"script_verification_engine"}:
        assert by_module[module].output_id is not None, module
    assert by_module["script_verification_engine"].output_id is None


# ---------------------------------------------------------------------------
# Section 7: no human-gate bypass
# ---------------------------------------------------------------------------


def test_idea_review_cannot_reach_r0_engine_without_approval(engine, project_at_idea_review):
    """The public engine surface itself blocks this -- R0ResearchEngine
    requires R0_RESEARCH, and the project is still in IDEA_REVIEW because
    approve_idea was never called."""
    provider = FakeLLMProvider([])
    retriever = FakeResearchRetriever([])
    r0_engine = R0ResearchEngine(engine, provider, retriever, fx.llm_settings())

    with pytest.raises(EngineStateError):
        r0_engine.run(R0ResearchInput(project_id=project_at_idea_review.project_id))

    assert get_project(engine, project_at_idea_review.project_id).state == ProjectState.IDEA_REVIEW
    assert provider.call_count == 0


def test_narrative_review_cannot_jump_directly_to_script(engine, project_at_narrative_review):
    """Pure topology fact (Phase 9 removed this edge) -- included here as an
    integration-level cross-check, not a redesign of the topology validator."""
    with pytest.raises(InvalidStateTransitionError):
        update_project_state(engine, project_at_narrative_review.project_id, ProjectState.SCRIPT)

    assert (
        get_project(engine, project_at_narrative_review.project_id).state
        == ProjectState.NARRATIVE_REVIEW
    )


def test_packaging_p0_does_not_advance_to_script_by_engine_execution_alone(
    engine, global_config, llm_settings, project_at_packaging_p0
):
    provider = FakeLLMProvider([fx.response(fx.packaging_prototype_json())])
    PackagingP0Engine(engine, provider, global_config, llm_settings).run(
        PackagingP0Input(project_id=project_at_packaging_p0.project_id)
    )

    assert get_project(engine, project_at_packaging_p0.project_id).state == ProjectState.PACKAGING_P0


def test_feasibility_does_not_advance_to_r1_by_engine_execution_alone(
    engine, global_config, llm_settings, project_at_feasibility
):
    provider = FakeLLMProvider([fx.response(fx.feasibility_report_json())])
    FeasibilityEngine(engine, provider, global_config, llm_settings).run(
        FeasibilityEngineInput(project_id=project_at_feasibility.project_id)
    )

    assert get_project(engine, project_at_feasibility.project_id).state == ProjectState.FEASIBILITY


def test_script_verification_does_not_advance_to_script_review_by_engine_execution_alone(
    engine, global_config, llm_settings, project_at_script_verification
):
    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="PASS"))])
    ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
        ScriptVerificationInput(project_id=project_at_script_verification.project_id)
    )

    assert (
        get_project(engine, project_at_script_verification.project_id).state
        == ProjectState.SCRIPT_VERIFICATION
    )


def test_script_review_cannot_reach_mvp_complete_without_final_approval(
    engine, global_config, llm_settings, project_at_script_review
):
    """No engine can be "run into" MVP_COMPLETE, and re-entering an earlier
    resolution action from SCRIPT_REVIEW is rejected by the state guard --
    approve_final_script is the only door."""
    provider = FakeLLMProvider([])
    with pytest.raises(EngineStateError):
        ScriptVerificationEngine(engine, provider, global_config, llm_settings).run(
            ScriptVerificationInput(project_id=project_at_script_review.project_id)
        )

    with pytest.raises(ReviewStateError):
        accept_script_verification(engine, project_at_script_review.project_id)

    assert get_project(engine, project_at_script_review.project_id).state == ProjectState.SCRIPT_REVIEW
