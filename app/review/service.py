"""Explicit human-review actions on an idea awaiting IDEA_REVIEW.

Deterministic application logic -- no LLM call, no BaseReview/Agent framework.
Ordering per stage: validate all preconditions first, then persist the
HumanApproval, then transition project state. If the state transition
unexpectedly fails after the approval was already persisted, that error
propagates as-is; no rollback is fabricated (see docs/TECHNICAL_SPEC_v0.1.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine

from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.models import SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
from app.models.approval import HumanApproval
from app.models.common import ApprovalStatus, GateStatus, ModuleRunStatus, ProjectState, RiskLevel, non_blank
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.review.errors import (
    FeasibilityDecisionMismatchError,
    MissingReviewArtifactError,
    PackagingRiskTooHighError,
    ReviewStateError,
    ScriptVerificationNotPassedError,
    StalePackagingPrototypeError,
    StaleScriptVerificationError,
)
from app.storage import approvals as approval_storage
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

_FEASIBILITY_TARGET_STATE = {
    GateStatus.PASS: ProjectState.R1_RESEARCH,
    GateStatus.REFRAME: ProjectState.IDEA_DISCOVERY,
    GateStatus.REJECT: ProjectState.ARCHIVED,
}
_FEASIBILITY_APPROVAL_STATUS = {
    GateStatus.PASS: ApprovalStatus.APPROVED,
    GateStatus.REFRAME: ApprovalStatus.REVISE,
    GateStatus.REJECT: ApprovalStatus.REJECTED,
}


def approve_idea(
    db_engine: Engine,
    project_id: UUID,
    feedback: str | None = None,
) -> HumanApproval:
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.IDEA_REVIEW:
        raise ReviewStateError(
            f"approve_idea requires project state IDEA_REVIEW, got {project.state.value}"
        )
    _verify_idea_candidate_artifact(db_engine, project)

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.IDEA_REVIEW,
        status=ApprovalStatus.APPROVED,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.R0_RESEARCH)
    return approval


def revise_idea(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.IDEA_REVIEW:
        raise ReviewStateError(
            f"revise_idea requires project state IDEA_REVIEW, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.IDEA_REVIEW,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.IDEA_DISCOVERY)
    return approval


def decide_feasibility(
    db_engine: Engine,
    project_id: UUID,
    decision: GateStatus,
    feedback: str | None = None,
) -> HumanApproval:
    """Route the project from FEASIBILITY based on an explicit human decision.

    decision must equal the stored FeasibilityReport.status exactly
    (FeasibilityDecisionMismatchError otherwise) -- Phase 6 does not support
    overriding the engine's own recommendation. PASS -> R1_RESEARCH,
    REFRAME -> IDEA_DISCOVERY (feedback required), REJECT -> ARCHIVED.
    Prior idea_candidate_id/research_r0_id/feasibility_id references are never
    cleared on REFRAME; they remain the most recent completed artifacts.
    """
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.FEASIBILITY:
        raise ReviewStateError(
            f"decide_feasibility requires project state FEASIBILITY, got {project.state.value}"
        )
    report = _verify_feasibility_report_artifact(db_engine, project)

    if decision != report.status:
        raise FeasibilityDecisionMismatchError(
            f"Decision {decision.value} does not match stored FeasibilityReport "
            f"status {report.status.value} for project {project_id}"
        )
    if decision is GateStatus.REFRAME:
        non_blank(feedback or "", "feedback")

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.FEASIBILITY,
        status=_FEASIBILITY_APPROVAL_STATUS[decision],
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, _FEASIBILITY_TARGET_STATE[decision])
    return approval


def _verify_feasibility_report_artifact(db_engine: Engine, project: Project) -> FeasibilityReport:
    if project.feasibility_id is None:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} has no feasibility_id reference"
        )
    try:
        report = artifact_storage.get_artifact(
            db_engine, project.project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport
        )
    except (ArtifactNotFoundError, ArtifactValidationError) as exc:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} feasibility_id references a missing or "
            f"invalid FeasibilityReport artifact"
        ) from exc
    if report.id != project.feasibility_id:
        raise MissingReviewArtifactError(
            f"Stored FeasibilityReport id does not match project.feasibility_id "
            f"for project {project.project_id}"
        )
    return report


def revise_narrative(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """NARRATIVE_REVIEW -> NARRATIVE. Does not auto-run NarrativeEngine; the
    user explicitly triggers regeneration later."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.NARRATIVE_REVIEW:
        raise ReviewStateError(
            f"revise_narrative requires project state NARRATIVE_REVIEW, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.NARRATIVE_REVIEW,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.NARRATIVE)
    return approval


def send_narrative_back_to_research(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """NARRATIVE_REVIEW -> R1_RESEARCH. Feedback required. Does not auto-run
    R1ResearchEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.NARRATIVE_REVIEW:
        raise ReviewStateError(
            f"send_narrative_back_to_research requires project state "
            f"NARRATIVE_REVIEW, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.NARRATIVE_REVIEW,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.R1_RESEARCH)
    return approval


def approve_narrative(
    db_engine: Engine,
    project_id: UUID,
    feedback: str | None = None,
) -> HumanApproval:
    """NARRATIVE_REVIEW -> PACKAGING_P0 (Phase 9 activates this route; the
    Phase 8 stub that always raised is gone). No Packaging P0 auto-run."""
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.NARRATIVE_REVIEW:
        raise ReviewStateError(
            f"approve_narrative requires project state NARRATIVE_REVIEW, got {project.state.value}"
        )
    _verify_narrative_plan_artifact(db_engine, project)

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.NARRATIVE_REVIEW,
        status=ApprovalStatus.APPROVED,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.PACKAGING_P0)
    return approval


def approve_packaging_p0(
    db_engine: Engine,
    project_id: UUID,
    feedback: str | None = None,
) -> HumanApproval:
    """PACKAGING_P0 -> SCRIPT. Blocked by PackagingRiskTooHighError when the
    stored PackagingPrototype.risk_of_misleading == HIGH -- a HIGH-risk
    prototype is a legitimate engine output, but a human cannot approve it
    forward in that form. No Script Engine auto-run."""
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.PACKAGING_P0:
        raise ReviewStateError(
            f"approve_packaging_p0 requires project state PACKAGING_P0, got {project.state.value}"
        )
    packaging = _verify_packaging_prototype_artifact(db_engine, project)
    _verify_packaging_is_fresh(db_engine, project)
    if packaging.risk_of_misleading is RiskLevel.HIGH:
        raise PackagingRiskTooHighError(
            f"Cannot approve PackagingPrototype with risk_of_misleading=HIGH for "
            f"project {project_id}; revise or send back to research first"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.PACKAGING_P0,
        status=ApprovalStatus.APPROVED,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.SCRIPT)
    return approval


def revise_packaging_p0(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """PACKAGING_P0 -> NARRATIVE: the promise/angle is weak because the
    narrative framing needs work. Does not auto-run NarrativeEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.PACKAGING_P0:
        raise ReviewStateError(
            f"revise_packaging_p0 requires project state PACKAGING_P0, got {project.state.value}"
        )
    _verify_packaging_prototype_artifact(db_engine, project)

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.PACKAGING_P0,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.NARRATIVE)
    return approval


def send_packaging_back_to_research(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """PACKAGING_P0 -> R1_RESEARCH: packaging exposed a factual promise risk
    that needs deeper research. Does not auto-run R1ResearchEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.PACKAGING_P0:
        raise ReviewStateError(
            f"send_packaging_back_to_research requires project state "
            f"PACKAGING_P0, got {project.state.value}"
        )
    _verify_packaging_prototype_artifact(db_engine, project)

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.PACKAGING_P0,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.R1_RESEARCH)
    return approval


def _verify_narrative_plan_artifact(db_engine: Engine, project: Project) -> NarrativePlan:
    if project.narrative_plan_id is None:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} has no narrative_plan_id reference"
        )
    try:
        plan = artifact_storage.get_artifact(
            db_engine, project.project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan
        )
    except (ArtifactNotFoundError, ArtifactValidationError) as exc:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} narrative_plan_id references a missing "
            f"or invalid NarrativePlan artifact"
        ) from exc
    if plan.id != project.narrative_plan_id:
        raise MissingReviewArtifactError(
            f"Stored NarrativePlan id does not match project.narrative_plan_id "
            f"for project {project.project_id}"
        )
    return plan


def _verify_packaging_prototype_artifact(db_engine: Engine, project: Project) -> PackagingPrototype:
    if project.packaging_prototype_id is None:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} has no packaging_prototype_id reference"
        )
    try:
        packaging = artifact_storage.get_artifact(
            db_engine, project.project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype
        )
    except (ArtifactNotFoundError, ArtifactValidationError) as exc:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} packaging_prototype_id references a "
            f"missing or invalid PackagingPrototype artifact"
        ) from exc
    if packaging.id != project.packaging_prototype_id:
        raise MissingReviewArtifactError(
            f"Stored PackagingPrototype id does not match "
            f"project.packaging_prototype_id for project {project.project_id}"
        )
    return packaging


def _verify_idea_candidate_artifact(db_engine: Engine, project: Project) -> None:
    if project.idea_candidate_id is None:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} has no idea_candidate_id reference"
        )
    try:
        idea = artifact_storage.get_artifact(
            db_engine, project.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate
        )
    except (ArtifactNotFoundError, ArtifactValidationError) as exc:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} idea_candidate_id references a missing or "
            f"invalid IdeaCandidate artifact"
        ) from exc
    if idea.id != project.idea_candidate_id:
        raise MissingReviewArtifactError(
            f"Stored IdeaCandidate id does not match project.idea_candidate_id for "
            f"project {project.project_id}"
        )


def accept_script_verification(
    db_engine: Engine,
    project_id: UUID,
    feedback: str | None = None,
) -> HumanApproval:
    """SCRIPT_VERIFICATION -> SCRIPT_REVIEW. Blocked by
    ScriptVerificationNotPassedError unless the current
    ScriptVerificationReport status is PASS -- a human cannot accept a script
    whose own verification found unresolved issues. No Script Review auto-run."""
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_VERIFICATION:
        raise ReviewStateError(
            f"accept_script_verification requires project state "
            f"SCRIPT_VERIFICATION, got {project.state.value}"
        )
    _verify_script_plan_artifact(db_engine, project)
    report = _get_current_script_verification_report(db_engine, project)
    _verify_script_verification_is_fresh(db_engine, project)
    if report.status is not GateStatus.PASS:
        raise ScriptVerificationNotPassedError(
            f"Cannot accept ScriptVerificationReport with status="
            f"{report.status.value} for project {project_id}; send for "
            f"rewrite or back to narrative/research first"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_VERIFICATION,
        status=ApprovalStatus.APPROVED,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.SCRIPT_REVIEW)
    return approval


def send_script_for_rewrite(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """SCRIPT_VERIFICATION -> SCRIPT: the verification found issues that need
    a script rewrite. Does not auto-run ScriptEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_VERIFICATION:
        raise ReviewStateError(
            f"send_script_for_rewrite requires project state "
            f"SCRIPT_VERIFICATION, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_VERIFICATION,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.SCRIPT)
    return approval


def send_script_back_to_narrative(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """SCRIPT_VERIFICATION -> NARRATIVE: the verification exposed a
    narrative-integrity problem the script alone cannot fix. Does not
    auto-run NarrativeEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_VERIFICATION:
        raise ReviewStateError(
            f"send_script_back_to_narrative requires project state "
            f"SCRIPT_VERIFICATION, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_VERIFICATION,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.NARRATIVE)
    return approval


def send_script_back_to_research(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """SCRIPT_VERIFICATION -> R1_RESEARCH: the verification exposed a factual
    problem that needs deeper research. Does not auto-run R1ResearchEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_VERIFICATION:
        raise ReviewStateError(
            f"send_script_back_to_research requires project state "
            f"SCRIPT_VERIFICATION, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_VERIFICATION,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.R1_RESEARCH)
    return approval


def approve_final_script(
    db_engine: Engine,
    project_id: UUID,
    feedback: str | None = None,
) -> HumanApproval:
    """SCRIPT_REVIEW -> MVP_COMPLETE: the final human sign-off that completes
    the original core MVP pipeline. Blocked by ScriptVerificationNotPassedError
    unless the current ScriptVerificationReport status is PASS -- mirrors
    accept_script_verification's guard, re-checked here since a report can
    only be superseded by a fresh, independently-gated run."""
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_REVIEW:
        raise ReviewStateError(
            f"approve_final_script requires project state SCRIPT_REVIEW, got "
            f"{project.state.value}"
        )
    _verify_script_plan_artifact(db_engine, project)
    report = _get_current_script_verification_report(db_engine, project)
    _verify_script_verification_is_fresh(db_engine, project)
    if report.status is not GateStatus.PASS:
        raise ScriptVerificationNotPassedError(
            f"Cannot give final approval with ScriptVerificationReport status="
            f"{report.status.value} for project {project_id}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_REVIEW,
        status=ApprovalStatus.APPROVED,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.MVP_COMPLETE)
    return approval


def revise_final_script(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """SCRIPT_REVIEW -> SCRIPT: final review found something worth a script
    rewrite. Does not auto-run ScriptEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_REVIEW:
        raise ReviewStateError(
            f"revise_final_script requires project state SCRIPT_REVIEW, got "
            f"{project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_REVIEW,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.SCRIPT)
    return approval


def send_final_script_back_to_narrative(
    db_engine: Engine,
    project_id: UUID,
    feedback: str,
) -> HumanApproval:
    """SCRIPT_REVIEW -> NARRATIVE: final review found a narrative-integrity
    problem the script alone cannot fix. Does not auto-run NarrativeEngine."""
    non_blank(feedback, "feedback")
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_REVIEW:
        raise ReviewStateError(
            f"send_final_script_back_to_narrative requires project state "
            f"SCRIPT_REVIEW, got {project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_REVIEW,
        status=ApprovalStatus.REVISE,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.NARRATIVE)
    return approval


def reject_final_script(
    db_engine: Engine,
    project_id: UUID,
    feedback: str | None = None,
) -> HumanApproval:
    """SCRIPT_REVIEW -> ARCHIVED: the project is abandoned at final review.
    Feedback is optional, unlike every other SCRIPT_REVIEW redirect -- a
    rejection needs no further routing information."""
    project = project_storage.get_project(db_engine, project_id)
    if project.state is not ProjectState.SCRIPT_REVIEW:
        raise ReviewStateError(
            f"reject_final_script requires project state SCRIPT_REVIEW, got "
            f"{project.state.value}"
        )

    approval = HumanApproval(
        project_id=project_id,
        stage=ProjectState.SCRIPT_REVIEW,
        status=ApprovalStatus.REJECTED,
        user_feedback=feedback,
        created_at=datetime.now(timezone.utc),
    )
    approval_storage.save_approval(db_engine, approval)
    project_storage.update_project_state(db_engine, project_id, ProjectState.ARCHIVED)
    return approval


def _verify_script_plan_artifact(db_engine: Engine, project: Project) -> ScriptPlan:
    if project.script_plan_id is None:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} has no script_plan_id reference"
        )
    try:
        script_plan = artifact_storage.get_artifact(
            db_engine, project.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan
        )
    except (ArtifactNotFoundError, ArtifactValidationError) as exc:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} script_plan_id references a missing "
            f"or invalid ScriptPlan artifact"
        ) from exc
    if script_plan.id != project.script_plan_id:
        raise MissingReviewArtifactError(
            f"Stored ScriptPlan id does not match project.script_plan_id for "
            f"project {project.project_id}"
        )
    return script_plan


def _latest_successful_module_run(db_engine: Engine, project_id: UUID, module: str):
    """The most recent SUCCESS ModuleRun for one module, or None. ModuleRun
    rows are returned in insertion (chronological) order by
    list_module_runs_for_project, so the last match is the most recent."""
    runs = module_run_storage.list_module_runs_for_project(db_engine, project_id)
    successful = [run for run in runs if run.module == module and run.status is ModuleRunStatus.SUCCESS]
    return successful[-1] if successful else None


def _verify_script_verification_is_fresh(db_engine: Engine, project: Project) -> None:
    """Phase 12 defect fix (docs/CORE_MVP_AUDIT_v0.1.md). ScriptVerificationReport
    has no id and no field referencing the ScriptPlan it audited (locked
    Phase 1 contract), so a report generated for a since-rewritten script
    could otherwise be reused to accept/approve the new, never-verified
    script. Freshness is established indirectly: the most recent successful
    script_verification_engine ModuleRun records the exact ScriptPlan.id it
    ran against (input_ids[3], per ScriptVerificationEngine.run()) -- that
    must match the ScriptPlan currently referenced by the project."""
    run = _latest_successful_module_run(db_engine, project.project_id, "script_verification_engine")
    if run is None or len(run.input_ids) < 4 or run.input_ids[3] != str(project.script_plan_id):
        raise StaleScriptVerificationError(
            f"The current ScriptVerificationReport for project {project.project_id} "
            f"was not generated for the current ScriptPlan "
            f"(script_plan_id={project.script_plan_id}); rerun Script "
            f"Verification first"
        )


def _verify_packaging_is_fresh(db_engine: Engine, project: Project) -> None:
    """Phase 12 defect fix (docs/CORE_MVP_AUDIT_v0.1.md). PackagingPrototype
    has no field referencing the NarrativePlan it was built from (locked
    contract), so a prototype generated for a since-revised narrative could
    otherwise be reused to approve packaging for a narrative it never
    actually evaluated. Freshness is established indirectly: the most
    recent successful packaging_p0_engine ModuleRun records the exact
    NarrativePlan.id it ran against (input_ids[2], per
    PackagingP0Engine.run()) -- that must match the NarrativePlan currently
    referenced by the project."""
    run = _latest_successful_module_run(db_engine, project.project_id, "packaging_p0_engine")
    if run is None or len(run.input_ids) < 3 or run.input_ids[2] != str(project.narrative_plan_id):
        raise StalePackagingPrototypeError(
            f"The current PackagingPrototype for project {project.project_id} "
            f"was not generated for the current NarrativePlan "
            f"(narrative_plan_id={project.narrative_plan_id}); rerun "
            f"Packaging P0 first"
        )


def _get_current_script_verification_report(
    db_engine: Engine, project: Project
) -> ScriptVerificationReport:
    """ScriptVerificationReport has no `id` field and Project has no
    verification-report reference field (see app/models/script.py,
    app/models/project.py) -- it is stored and retrieved by artifact type
    alone, so there is no id to cross-check against."""
    try:
        return artifact_storage.get_artifact(
            db_engine,
            project.project_id,
            SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
            ScriptVerificationReport,
        )
    except (ArtifactNotFoundError, ArtifactValidationError) as exc:
        raise MissingReviewArtifactError(
            f"Project {project.project_id} has no valid ScriptVerificationReport "
            f"artifact"
        ) from exc
