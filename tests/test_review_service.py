from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.models import SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
from app.models.common import (
    ApprovalStatus,
    GateEvaluation,
    GateStatus,
    ModuleRunStatus,
    PrimaryPayoff,
    ProjectState,
)
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.review.errors import (
    FeasibilityDecisionMismatchError,
    MissingReviewArtifactError,
    PackagingRiskTooHighError,
    ReviewStateError,
    ScriptVerificationNotPassedError,
    StalePackagingPrototypeError,
    StaleScriptVerificationError,
)
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
    reject_final_script,
    revise_final_script,
    revise_idea,
    revise_narrative,
    revise_packaging_p0,
    send_final_script_back_to_narrative,
    send_narrative_back_to_research,
    send_packaging_back_to_research,
    send_script_back_to_narrative,
    send_script_back_to_research,
    send_script_for_rewrite,
)
from app.storage.approvals import list_approvals_for_project
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import save_module_run
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state


def _valid_idea_candidate() -> IdeaCandidate:
    return IdeaCandidate(
        topic="Cau Tacoma Narrows",
        central_question="Vi sao mot cay cau vung chac lai sup do vi gio nhe?",
        abt=ABT(
            and_context="Ky su tin rang cau treo da du an toan",
            but_complication="Cau rung lac du doi roi sup do duoi con gio vua phai",
            therefore_investigation="Dieu tra co che cong huong khi dong hoc",
        ),
        primary_payoff=PrimaryPayoff.REVERSAL,
        physics_core="Cong huong khi dong hoc tu kich thich",
        audience_prerequisite="none",
        brand_fit=GateEvaluation(status="PASS", reason="ok"),
        general_audience_gate=GateEvaluation(status="PASS", reason="ok"),
        longform_potential=GateEvaluation(status="PASS", reason="ok"),
    )


def _create_project(engine, state=ProjectState.NEW_PROJECT):
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01", created_at=created, updated_at=created, state=ProjectState.NEW_PROJECT
    )
    create_project(engine, project)
    if state is not ProjectState.NEW_PROJECT:
        update_project_state(engine, project.project_id, ProjectState.IDEA_DISCOVERY)
        if state is ProjectState.IDEA_REVIEW:
            update_project_state(engine, project.project_id, ProjectState.IDEA_REVIEW)
    return project.project_id


def _create_project_in_idea_review_with_artifact(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    idea = _valid_idea_candidate()
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    return project_id, idea


def _valid_research_r0(idea_id) -> ResearchR0:
    return ResearchR0(
        idea_id=idea_id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        initial_findings=["Flutter is a documented bridge failure mode"],
        candidate_sources=["https://real.example/source"],
        major_risks=["Risk of conflating flutter with resonance"],
        recommendation="CONTINUE",
    )


def _valid_feasibility_report(status: str = "PASS") -> FeasibilityReport:
    return FeasibilityReport(
        status=status,
        audience=SubEvaluation(status=status, reason="r"),
        science=SubEvaluation(status=status, reason="r"),
        narrative=SubEvaluation(status=status, reason="r"),
        visual=SubEvaluation(status=status, reason="r"),
        production=ProductionEvaluation(status=status, estimated_complexity="LOW", reason="r"),
    )


def _create_project_in_feasibility_with_report(engine, report_status: str = "PASS"):
    project_id, idea = _create_project_in_idea_review_with_artifact(engine)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)

    research = _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research)
    update_artifact_reference(engine, project_id, "research_r0_id", research.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    report = _valid_feasibility_report(status=report_status)
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, report)
    update_artifact_reference(engine, project_id, "feasibility_id", report.id)

    return project_id, report


def _valid_research_package(central_question: str) -> ResearchPackage:
    return ResearchPackage(
        central_question=central_question,
        executive_summary="Flutter caused the bridge to collapse.",
        physics_core="Cong huong khi dong hoc tu kich thich",
        simplification_boundary="1. safe_model: ... 2. allowed_simplifications: ...",
        claims=[
            Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")
        ],
    )


def _valid_narrative_plan(central_question: str) -> NarrativePlan:
    return NarrativePlan(
        central_question=central_question,
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(
                id="Q0",
                question="Why?",
                why_viewer_cares="It matters",
                partial_answer="Flutter",
                claim_ids=["C001"],
                creates_next_question=None,
                information_gap="none left",
            )
        ],
        ti_role="Investigator",
        ending="Callback",
        claim_ids_used=["C001"],
    )


def _create_project_in_narrative_review_with_plan(engine):
    project_id, idea = _create_project_in_idea_review_with_artifact(engine)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)

    research_r0 = _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = _valid_feasibility_report(status="PASS")
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    research_package = _valid_research_package(idea.central_question)
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)

    plan = _valid_narrative_plan(idea.central_question)
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)

    return project_id, plan


def _valid_packaging_prototype(risk: str = "LOW") -> PackagingPrototype:
    return PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading=risk,
    )


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]):
    """Phase 12's review-layer freshness checks (app/review/service.py) read
    the most recent successful ModuleRun for the generating engine to prove
    an artifact was built from the project's *current* upstream reference.
    These fixtures build artifacts directly (bypassing the real engine) to
    keep review-layer unit tests independent of engine behavior, so a
    plausible ModuleRun record is added here to match what a real run would
    have left behind. Only the position(s) the freshness checks actually
    read need to be real ids; the rest are placeholders."""
    now = datetime.now(timezone.utc)
    save_module_run(
        engine,
        ModuleRun(
            project_id=project_id,
            module=module,
            module_version="0.1",
            started_at=now,
            completed_at=now,
            input_ids=input_ids,
            status=ModuleRunStatus.SUCCESS,
        ),
    )


def _create_project_in_packaging_p0_with_prototype(engine, risk: str = "LOW"):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)

    prototype = _valid_packaging_prototype(risk=risk)
    save_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, prototype)
    update_artifact_reference(engine, project_id, "packaging_prototype_id", prototype.id)
    # PackagingP0Engine records input_ids=[idea.id, research_package.id,
    # narrative_plan.id] -- index 2 is what the freshness check reads.
    _record_module_run_success(
        engine, project_id, "packaging_p0_engine", [str(uuid4()), str(uuid4()), str(plan.id)]
    )

    return project_id, prototype


def _valid_script_plan(claim_ids=("C001",)) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q0",
                narrative_function="INFORM",
                lines=[
                    ScriptLine(
                        line_id="L001",
                        text="Tí kể một chuyện thú vị cho bạn nghe.",
                        function="INFORM",
                        claim_ids=list(claim_ids),
                    )
                ],
            )
        ],
        claim_coverage=["C001"],
        qa_status="PASS",
    )


def _valid_script_verification_report(status: str = "PASS") -> ScriptVerificationReport:
    if status == "PASS":
        return ScriptVerificationReport(status="PASS")
    return ScriptVerificationReport(status=status, unsupported_lines=["L001: needs stronger support"])


def _create_project_in_script_verification_with_report(engine, report_status: str = "PASS"):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    script_plan = _valid_script_plan()
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan.id)
    update_project_state(engine, project_id, ProjectState.SCRIPT_VERIFICATION)

    report = _valid_script_verification_report(status=report_status)
    save_artifact(engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, report)
    # ScriptVerificationEngine records input_ids=[research_package.id,
    # narrative_plan.id, packaging.id, script_plan.id] -- index 3 is what
    # the freshness check reads.
    _record_module_run_success(
        engine,
        project_id,
        "script_verification_engine",
        [str(uuid4()), str(uuid4()), str(uuid4()), str(script_plan.id)],
    )

    return project_id, script_plan, report


def _create_project_in_script_review_with_report(engine, report_status: str = "PASS"):
    project_id, script_plan, report = _create_project_in_script_verification_with_report(
        engine, report_status=report_status
    )
    update_project_state(engine, project_id, ProjectState.SCRIPT_REVIEW)
    return project_id, script_plan, report


# ---------------------------------------------------------------------------
# Section 30: approval
# ---------------------------------------------------------------------------


def test_approve_idea_transitions_to_r0_research(engine):
    project_id, idea = _create_project_in_idea_review_with_artifact(engine)

    approval = approve_idea(engine, project_id, feedback="Looks great")

    assert approval.status == ApprovalStatus.APPROVED
    assert approval.stage == ProjectState.IDEA_REVIEW
    assert approval.user_feedback == "Looks great"

    stored = list_approvals_for_project(engine, project_id)
    assert len(stored) == 1
    assert stored[0].approval_id == approval.approval_id

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R0_RESEARCH
    assert project.idea_candidate_id == idea.id  # unchanged


def test_approve_idea_allows_no_feedback(engine):
    project_id, _ = _create_project_in_idea_review_with_artifact(engine)
    approval = approve_idea(engine, project_id)
    assert approval.user_feedback is None


# ---------------------------------------------------------------------------
# Section 31: revision
# ---------------------------------------------------------------------------


def test_revise_idea_returns_to_idea_discovery(engine):
    project_id, idea = _create_project_in_idea_review_with_artifact(engine)

    approval = revise_idea(engine, project_id, feedback="Weak central question")

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.IDEA_REVIEW
    assert approval.user_feedback == "Weak central question"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_DISCOVERY

    from app.storage.artifacts import get_artifact

    still_there = get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
    assert still_there == idea


def test_revise_idea_requires_non_blank_feedback(engine):
    project_id, _ = _create_project_in_idea_review_with_artifact(engine)
    with pytest.raises(ValueError):
        revise_idea(engine, project_id, feedback="   ")


# ---------------------------------------------------------------------------
# Section 32: wrong state
# ---------------------------------------------------------------------------


def test_approve_idea_wrong_state_rejected(engine):
    project_id = _create_project(engine, state=ProjectState.NEW_PROJECT)
    with pytest.raises(ReviewStateError):
        approve_idea(engine, project_id)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.NEW_PROJECT


def test_revise_idea_wrong_state_rejected(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    with pytest.raises(ReviewStateError):
        revise_idea(engine, project_id, feedback="anything")
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY


# ---------------------------------------------------------------------------
# Section 33: missing artifact
# ---------------------------------------------------------------------------


def test_approve_idea_missing_idea_candidate_id_rejected(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_REVIEW)
    with pytest.raises(MissingReviewArtifactError):
        approve_idea(engine, project_id)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_REVIEW


def test_approve_idea_mismatched_artifact_id_rejected(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    idea = _valid_idea_candidate()
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    # Point the project at a different, never-persisted idea id.
    update_artifact_reference(engine, project_id, "idea_candidate_id", uuid4())
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)

    with pytest.raises(MissingReviewArtifactError):
        approve_idea(engine, project_id)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_REVIEW


# ---------------------------------------------------------------------------
# Section 37: human PASS decision
# ---------------------------------------------------------------------------


def test_decide_feasibility_pass(engine):
    project_id, report = _create_project_in_feasibility_with_report(engine, report_status="PASS")

    approval = decide_feasibility(engine, project_id, GateStatus.PASS, feedback="Great, proceed")

    assert approval.status == ApprovalStatus.APPROVED
    assert approval.stage == ProjectState.FEASIBILITY

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH

    from app.storage.artifacts import get_artifact

    stored = get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
    assert stored == report


# ---------------------------------------------------------------------------
# Section 38/39: human REFRAME decision
# ---------------------------------------------------------------------------


def test_decide_feasibility_reframe(engine):
    project_id, report = _create_project_in_feasibility_with_report(engine, report_status="REFRAME")

    approval = decide_feasibility(
        engine, project_id, GateStatus.REFRAME, feedback="Needs a sharper central question"
    )

    assert approval.status == ApprovalStatus.REVISE
    assert approval.user_feedback == "Needs a sharper central question"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_DISCOVERY
    # Prior references are retained, not cleared.
    assert project.idea_candidate_id is not None
    assert project.research_r0_id is not None
    assert project.feasibility_id is not None


def test_decide_feasibility_reframe_requires_non_blank_feedback(engine):
    project_id, report = _create_project_in_feasibility_with_report(engine, report_status="REFRAME")

    with pytest.raises(ValueError):
        decide_feasibility(engine, project_id, GateStatus.REFRAME, feedback=None)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY


# ---------------------------------------------------------------------------
# Section 40: human REJECT decision
# ---------------------------------------------------------------------------


def test_decide_feasibility_reject(engine):
    project_id, report = _create_project_in_feasibility_with_report(engine, report_status="REJECT")

    approval = decide_feasibility(engine, project_id, GateStatus.REJECT, feedback="Not worth pursuing")

    assert approval.status == ApprovalStatus.REJECTED

    project = get_project(engine, project_id)
    assert project.state == ProjectState.ARCHIVED


# ---------------------------------------------------------------------------
# Section 41: decision mismatch
# ---------------------------------------------------------------------------


def test_decide_feasibility_mismatch_reframe_rejected(engine):
    project_id, report = _create_project_in_feasibility_with_report(engine, report_status="PASS")

    with pytest.raises(FeasibilityDecisionMismatchError):
        decide_feasibility(engine, project_id, GateStatus.REFRAME, feedback="trying to override")

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY


def test_decide_feasibility_mismatch_reject_rejected(engine):
    project_id, report = _create_project_in_feasibility_with_report(engine, report_status="PASS")

    with pytest.raises(FeasibilityDecisionMismatchError):
        decide_feasibility(engine, project_id, GateStatus.REJECT)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY


# ---------------------------------------------------------------------------
# Section 42: decision wrong state
# ---------------------------------------------------------------------------


def test_decide_feasibility_wrong_state(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)

    with pytest.raises(ReviewStateError):
        decide_feasibility(engine, project_id, GateStatus.PASS)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY


# ---------------------------------------------------------------------------
# Section 43: decision missing report
# ---------------------------------------------------------------------------


def test_decide_feasibility_missing_report(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    with pytest.raises(MissingReviewArtifactError):
        decide_feasibility(engine, project_id, GateStatus.PASS)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY


# ---------------------------------------------------------------------------
# Section 54: review REVISE (NARRATIVE_REVIEW -> NARRATIVE)
# ---------------------------------------------------------------------------


def test_revise_narrative_returns_to_narrative(engine):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)

    approval = revise_narrative(engine, project_id, feedback="Ladder feels rushed")

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.NARRATIVE_REVIEW
    assert approval.user_feedback == "Ladder feels rushed"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.NARRATIVE

    # The prior NarrativePlan artifact remains available; no auto-run occurred.
    still_there = get_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
    assert still_there == plan


def test_revise_narrative_requires_non_blank_feedback(engine):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)
    with pytest.raises(ValueError):
        revise_narrative(engine, project_id, feedback="   ")
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE_REVIEW


# ---------------------------------------------------------------------------
# Section 55: review BACK_TO_RESEARCH (NARRATIVE_REVIEW -> R1_RESEARCH)
# ---------------------------------------------------------------------------


def test_send_narrative_back_to_research(engine):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)

    approval = send_narrative_back_to_research(
        engine, project_id, feedback="Need stronger source support for the flutter claim"
    )

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.NARRATIVE_REVIEW
    assert approval.user_feedback == "Need stronger source support for the flutter claim"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH


def test_send_narrative_back_to_research_requires_feedback(engine):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)
    with pytest.raises(ValueError):
        send_narrative_back_to_research(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE_REVIEW


# ---------------------------------------------------------------------------
# Phase 9, Section 31: narrative approval activated (replaces the Phase 8 stub)
# ---------------------------------------------------------------------------


def test_approve_narrative_transitions_to_packaging_p0(engine):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)

    approval = approve_narrative(engine, project_id, feedback="Strong investigative arc")

    assert approval.status == ApprovalStatus.APPROVED
    assert approval.stage == ProjectState.NARRATIVE_REVIEW
    assert approval.user_feedback == "Strong investigative arc"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.PACKAGING_P0

    # No Packaging engine auto-run: no packaging artifact exists yet.
    assert project.packaging_prototype_id is None


def test_approve_narrative_missing_plan_rejected(engine):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)

    with pytest.raises(MissingReviewArtifactError):
        approve_narrative(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.NARRATIVE_REVIEW


# ---------------------------------------------------------------------------
# Section 39: human approve packaging (LOW/MEDIUM risk)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("risk", ["LOW", "MEDIUM"])
def test_approve_packaging_p0_transitions_to_script(engine, risk):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine, risk=risk)

    approval = approve_packaging_p0(engine, project_id, feedback="Ship it")

    assert approval.status == ApprovalStatus.APPROVED
    assert approval.stage == ProjectState.PACKAGING_P0

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT


# ---------------------------------------------------------------------------
# Section 40: HIGH-risk approval block
# ---------------------------------------------------------------------------


def test_approve_packaging_p0_high_risk_blocked(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine, risk="HIGH")

    with pytest.raises(PackagingRiskTooHighError):
        approve_packaging_p0(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0


# ---------------------------------------------------------------------------
# Section 41: packaging REVISE
# ---------------------------------------------------------------------------


def test_revise_packaging_p0_returns_to_narrative(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine, risk="HIGH")

    approval = revise_packaging_p0(engine, project_id, feedback="Angle overpromises the reveal")

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.PACKAGING_P0
    assert approval.user_feedback == "Angle overpromises the reveal"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.NARRATIVE


def test_revise_packaging_p0_requires_feedback(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine)
    with pytest.raises(ValueError):
        revise_packaging_p0(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0


# ---------------------------------------------------------------------------
# Section 42: packaging back to research
# ---------------------------------------------------------------------------


def test_send_packaging_back_to_research(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine)

    approval = send_packaging_back_to_research(
        engine, project_id, feedback="Promise depends on an unverified factual claim"
    )

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.PACKAGING_P0

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH


def test_send_packaging_back_to_research_requires_feedback(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine)
    with pytest.raises(ValueError):
        send_packaging_back_to_research(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0


# ---------------------------------------------------------------------------
# Section 43: packaging review wrong state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        lambda engine, project_id: approve_packaging_p0(engine, project_id),
        lambda engine, project_id: revise_packaging_p0(engine, project_id, feedback="x"),
        lambda engine, project_id: send_packaging_back_to_research(engine, project_id, feedback="x"),
    ],
)
def test_packaging_review_wrong_state_rejected(engine, action):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    with pytest.raises(ReviewStateError):
        action(engine, project_id)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY


# ---------------------------------------------------------------------------
# Section 44: missing packaging artifact
# ---------------------------------------------------------------------------


def test_approve_packaging_p0_missing_artifact_rejected(engine):
    project_id, plan = _create_project_in_narrative_review_with_plan(engine)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)

    with pytest.raises(MissingReviewArtifactError):
        approve_packaging_p0(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0


# ---------------------------------------------------------------------------
# Section 56: accept script verification (PASS)
# ---------------------------------------------------------------------------


def test_accept_script_verification_transitions_to_script_review(engine):
    project_id, script_plan, report = _create_project_in_script_verification_with_report(
        engine, report_status="PASS"
    )

    approval = accept_script_verification(engine, project_id, feedback="Verification is clean")

    assert approval.status == ApprovalStatus.APPROVED
    assert approval.stage == ProjectState.SCRIPT_VERIFICATION
    assert approval.user_feedback == "Verification is clean"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT_REVIEW

    # No auto-run: the ScriptPlan artifact is untouched.
    still_there = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert still_there == script_plan


def test_accept_script_verification_allows_no_feedback(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine, report_status="PASS")
    approval = accept_script_verification(engine, project_id)
    assert approval.user_feedback is None


# ---------------------------------------------------------------------------
# Section 57: accept script verification blocked (non-PASS)
# ---------------------------------------------------------------------------


def test_accept_script_verification_blocked_when_not_passed(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine, report_status="REFRAME")

    with pytest.raises(ScriptVerificationNotPassedError):
        accept_script_verification(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


# ---------------------------------------------------------------------------
# Section 58: send script for rewrite
# ---------------------------------------------------------------------------


def test_send_script_for_rewrite_returns_to_script(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine, report_status="REFRAME")

    approval = send_script_for_rewrite(engine, project_id, feedback="Fix the unsupported claim in L001")

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.SCRIPT_VERIFICATION
    assert approval.user_feedback == "Fix the unsupported claim in L001"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT


def test_send_script_for_rewrite_requires_feedback(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine)
    with pytest.raises(ValueError):
        send_script_for_rewrite(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


# ---------------------------------------------------------------------------
# Section 59: send script back to narrative / research
# ---------------------------------------------------------------------------


def test_send_script_back_to_narrative(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine, report_status="REJECT")

    approval = send_script_back_to_narrative(
        engine, project_id, feedback="The resolution no longer matches the approved narrative"
    )

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.SCRIPT_VERIFICATION

    project = get_project(engine, project_id)
    assert project.state == ProjectState.NARRATIVE


def test_send_script_back_to_narrative_requires_feedback(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine)
    with pytest.raises(ValueError):
        send_script_back_to_narrative(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


def test_send_script_back_to_research(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine, report_status="REJECT")

    approval = send_script_back_to_research(
        engine, project_id, feedback="The claim itself needs deeper sourcing"
    )

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.SCRIPT_VERIFICATION

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH


def test_send_script_back_to_research_requires_feedback(engine):
    project_id, *_ = _create_project_in_script_verification_with_report(engine)
    with pytest.raises(ValueError):
        send_script_back_to_research(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


# ---------------------------------------------------------------------------
# Section 60: script verification review wrong state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        lambda engine, project_id: accept_script_verification(engine, project_id),
        lambda engine, project_id: send_script_for_rewrite(engine, project_id, feedback="x"),
        lambda engine, project_id: send_script_back_to_narrative(engine, project_id, feedback="x"),
        lambda engine, project_id: send_script_back_to_research(engine, project_id, feedback="x"),
    ],
)
def test_script_verification_review_wrong_state_rejected(engine, action):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    with pytest.raises(ReviewStateError):
        action(engine, project_id)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY


# ---------------------------------------------------------------------------
# Section 61: accept script verification missing artifact
# ---------------------------------------------------------------------------


def test_accept_script_verification_missing_script_plan_rejected(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine)
    update_project_state(engine, project_id, ProjectState.SCRIPT)
    update_project_state(engine, project_id, ProjectState.SCRIPT_VERIFICATION)

    with pytest.raises(MissingReviewArtifactError):
        accept_script_verification(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


def test_accept_script_verification_missing_report_rejected(engine):
    project_id, prototype = _create_project_in_packaging_p0_with_prototype(engine)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    script_plan = _valid_script_plan()
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan.id)
    update_project_state(engine, project_id, ProjectState.SCRIPT_VERIFICATION)
    # ScriptPlan exists but no ScriptVerificationReport artifact was saved.

    with pytest.raises(MissingReviewArtifactError):
        accept_script_verification(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_VERIFICATION


# ---------------------------------------------------------------------------
# Section 62: final approval (PASS) -> MVP_COMPLETE
# ---------------------------------------------------------------------------


def test_approve_final_script_transitions_to_mvp_complete(engine):
    project_id, script_plan, report = _create_project_in_script_review_with_report(
        engine, report_status="PASS"
    )

    approval = approve_final_script(engine, project_id, feedback="Ready to publish")

    assert approval.status == ApprovalStatus.APPROVED
    assert approval.stage == ProjectState.SCRIPT_REVIEW
    assert approval.user_feedback == "Ready to publish"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE


def test_approve_final_script_allows_no_feedback(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")
    approval = approve_final_script(engine, project_id)
    assert approval.user_feedback is None


# ---------------------------------------------------------------------------
# Section 63: final approval blocked (non-PASS)
# ---------------------------------------------------------------------------


def test_approve_final_script_blocked_when_not_passed(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="REFRAME")

    with pytest.raises(ScriptVerificationNotPassedError):
        approve_final_script(engine, project_id)

    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_REVIEW


# ---------------------------------------------------------------------------
# Section 64: final revise / back to narrative / reject
# ---------------------------------------------------------------------------


def test_revise_final_script_returns_to_script(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")

    approval = revise_final_script(engine, project_id, feedback="Tighten the ending")

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.SCRIPT_REVIEW

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT


def test_revise_final_script_requires_feedback(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")
    with pytest.raises(ValueError):
        revise_final_script(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_REVIEW


def test_send_final_script_back_to_narrative(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")

    approval = send_final_script_back_to_narrative(
        engine, project_id, feedback="Final read shows a narrative-integrity gap"
    )

    assert approval.status == ApprovalStatus.REVISE
    assert approval.stage == ProjectState.SCRIPT_REVIEW

    project = get_project(engine, project_id)
    assert project.state == ProjectState.NARRATIVE


def test_send_final_script_back_to_narrative_requires_feedback(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")
    with pytest.raises(ValueError):
        send_final_script_back_to_narrative(engine, project_id, feedback=None)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_REVIEW


def test_reject_final_script_archives_project(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")

    approval = reject_final_script(engine, project_id, feedback="Project abandoned")

    assert approval.status == ApprovalStatus.REJECTED
    assert approval.stage == ProjectState.SCRIPT_REVIEW
    assert approval.user_feedback == "Project abandoned"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.ARCHIVED


def test_reject_final_script_allows_no_feedback(engine):
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="PASS")
    approval = reject_final_script(engine, project_id)
    assert approval.user_feedback is None
    assert get_project(engine, project_id).state == ProjectState.ARCHIVED


def test_reject_final_script_works_even_when_not_passed(engine):
    """Unlike approve_final_script, rejection needs no PASS guard -- a human
    can always abandon the project at final review."""
    project_id, *_ = _create_project_in_script_review_with_report(engine, report_status="REJECT")
    approval = reject_final_script(engine, project_id)
    assert approval.status == ApprovalStatus.REJECTED
    assert get_project(engine, project_id).state == ProjectState.ARCHIVED


# ---------------------------------------------------------------------------
# Section 65: final script review wrong state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        lambda engine, project_id: approve_final_script(engine, project_id),
        lambda engine, project_id: revise_final_script(engine, project_id, feedback="x"),
        lambda engine, project_id: send_final_script_back_to_narrative(engine, project_id, feedback="x"),
        lambda engine, project_id: reject_final_script(engine, project_id),
    ],
)
def test_final_script_review_wrong_state_rejected(engine, action):
    project_id = _create_project(engine, state=ProjectState.IDEA_DISCOVERY)
    with pytest.raises(ReviewStateError):
        action(engine, project_id)
    assert list_approvals_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_DISCOVERY
