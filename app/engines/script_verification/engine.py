"""ScriptVerificationEngine: an independent scientific/narrative/promise audit
of the locked ScriptPlan -- eighth vertical slice.

SCRIPT_VERIFICATION -> load+verify ResearchPackage/NarrativePlan/
PackagingPrototype/ScriptPlan -> generate_structured(ScriptVerificationReport)
-> deterministic status normalization (always applied) -> defense-in-depth
claim-reference pre-check against the ScriptPlan -> one bounded correction
attempt if the LLM did not acknowledge a known-bad line -> persist artifact
-> persist ModuleRun.

IMPORTANT: this engine never modifies or re-saves the ScriptPlan it reviews
(the reviewed script's immutability is the whole point of the audit), never
updates any Project reference field (no verification-report reference field
exists on Project), and never transitions project state -- the project stays
in SCRIPT_VERIFICATION, awaiting an explicit human resolution decision (see
app/review/service.py). No Voice/Visual module, no orchestrator, no
BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.errors import (
    MissingNarrativePlanArtifactError,
    MissingPackagingPrototypeArtifactError,
    MissingResearchPackageArtifactError,
    MissingScriptPlanArtifactError,
    ScriptVerificationBusinessError,
)
from app.engines.script_verification.models import (
    SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
    ScriptVerificationInput,
    ScriptVerificationResult,
)
from app.engines.script_verification.prompt import (
    build_correction_request,
    build_system_prompt,
    build_user_prompt,
)
from app.engines.script_verification.validation import (
    find_deterministic_claim_issues,
    find_unacknowledged_claim_issues,
    normalize_status,
)
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import ResearchPackage
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "script_verification_engine"
MODULE_VERSION = "0.1"

# "One bounded correction attempt" -- exactly one extra provider call, with no
# further JSON/schema retries of its own, if the LLM did not acknowledge a
# deterministically-known claim-reference issue.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class ScriptVerificationEngine:
    def __init__(
        self,
        db_engine: Engine,
        provider: LLMProvider,
        global_config: GlobalConfig,
        llm_settings: LLMSettings,
        *,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._provider = provider
        self._global_config = global_config
        self._llm_settings = llm_settings
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, engine_input: ScriptVerificationInput) -> ScriptVerificationResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.SCRIPT_VERIFICATION:
            raise EngineStateError(
                "ScriptVerificationEngine requires project state SCRIPT_VERIFICATION, "
                f"got {project.state.value}"
            )
        research_package = self._load_and_verify_research_package(project)
        narrative_plan = self._load_and_verify_narrative_plan(project)
        packaging = self._load_and_verify_packaging_prototype(project)
        script_plan = self._load_and_verify_script_plan(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[
                str(research_package.id),
                str(narrative_plan.id),
                str(packaging.id),
                str(script_plan.id),
            ],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            report, generation_attempts, response, business_correction_used = self._generate_report(
                script_plan, research_package, narrative_plan, packaging, engine_input.additional_context
            )

            self._artifact_repo.save_artifact(
                self._db_engine,
                engine_input.project_id,
                SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
                report,
            )
            # No Project reference field update: no verification-report
            # reference field exists on Project (Phase 11 compat finding).
            # No state transition: the project stays in SCRIPT_VERIFICATION
            # awaiting an explicit human resolution decision -- mirrors
            # PackagingP0Engine.
        except Exception as exc:
            # Broad on purpose: any failure from generation through
            # persistence must be recorded as a FAILED audit record and
            # re-raised unchanged -- mirrors every prior engine.
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(self._db_engine, _with_success(run_record))

        return ScriptVerificationResult(
            report=report,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
            business_correction_used=business_correction_used,
        )

    def _load_and_verify_research_package(self, project: Project) -> ResearchPackage:
        if project.research_r1_id is None:
            raise MissingResearchPackageArtifactError(
                f"Project {project.project_id} has no research_r1_id reference"
            )
        try:
            research_package = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingResearchPackageArtifactError(
                f"Project {project.project_id} research_r1_id references a "
                f"missing or invalid ResearchPackage artifact"
            ) from exc
        if research_package.id != project.research_r1_id:
            raise MissingResearchPackageArtifactError(
                f"Stored ResearchPackage id does not match project.research_r1_id "
                f"for project {project.project_id}"
            )
        return research_package

    def _load_and_verify_narrative_plan(self, project: Project) -> NarrativePlan:
        if project.narrative_plan_id is None:
            raise MissingNarrativePlanArtifactError(
                f"Project {project.project_id} has no narrative_plan_id reference"
            )
        try:
            narrative_plan = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingNarrativePlanArtifactError(
                f"Project {project.project_id} narrative_plan_id references a "
                f"missing or invalid NarrativePlan artifact"
            ) from exc
        if narrative_plan.id != project.narrative_plan_id:
            raise MissingNarrativePlanArtifactError(
                f"Stored NarrativePlan id does not match project.narrative_plan_id "
                f"for project {project.project_id}"
            )
        return narrative_plan

    def _load_and_verify_packaging_prototype(self, project: Project) -> PackagingPrototype:
        if project.packaging_prototype_id is None:
            raise MissingPackagingPrototypeArtifactError(
                f"Project {project.project_id} has no packaging_prototype_id reference"
            )
        try:
            packaging = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingPackagingPrototypeArtifactError(
                f"Project {project.project_id} packaging_prototype_id references a "
                f"missing or invalid PackagingPrototype artifact"
            ) from exc
        if packaging.id != project.packaging_prototype_id:
            raise MissingPackagingPrototypeArtifactError(
                f"Stored PackagingPrototype id does not match "
                f"project.packaging_prototype_id for project {project.project_id}"
            )
        return packaging

    def _load_and_verify_script_plan(self, project: Project) -> ScriptPlan:
        if project.script_plan_id is None:
            raise MissingScriptPlanArtifactError(
                f"Project {project.project_id} has no script_plan_id reference"
            )
        try:
            script_plan = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingScriptPlanArtifactError(
                f"Project {project.project_id} script_plan_id references a "
                f"missing or invalid ScriptPlan artifact"
            ) from exc
        if script_plan.id != project.script_plan_id:
            raise MissingScriptPlanArtifactError(
                f"Stored ScriptPlan id does not match project.script_plan_id "
                f"for project {project.project_id}"
            )
        return script_plan

    def _generate_report(
        self,
        script_plan: ScriptPlan,
        research_package: ResearchPackage,
        narrative_plan: NarrativePlan,
        packaging: PackagingPrototype,
        additional_context: str | None,
    ):
        deterministic_bad_line_ids = find_deterministic_claim_issues(script_plan, research_package)

        request = LLMRequest(
            system_prompt=build_system_prompt(
                self._global_config, research_package, narrative_plan, packaging
            ),
            user_prompt=build_user_prompt(script_plan, deterministic_bad_line_ids, additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            ScriptVerificationReport,
            max_retries=self._llm_settings.max_structured_retries,
        )
        report = normalize_status(generation.value)
        attempts = generation.attempts
        response = generation.response
        business_correction_used = False

        unacknowledged = find_unacknowledged_claim_issues(report, deterministic_bad_line_ids)
        if unacknowledged:
            business_correction_used = True
            correction_request = build_correction_request(request, unacknowledged)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                ScriptVerificationReport,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            report = normalize_status(correction_generation.value)
            attempts += correction_generation.attempts
            response = correction_generation.response

            unacknowledged = find_unacknowledged_claim_issues(report, deterministic_bad_line_ids)
            if unacknowledged:
                raise ScriptVerificationBusinessError(unacknowledged)

        return report, attempts, response, business_correction_used


def _with_success(run_record: ModuleRun) -> ModuleRun:
    # output_id stays None: the locked ScriptVerificationReport has no `id`
    # field to reference (see app/models/script.py).
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        status=ModuleRunStatus.SUCCESS,
    )


def _with_failure(run_record: ModuleRun, exc: Exception) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        status=ModuleRunStatus.FAILED,
        error_message=_safe_error_message(exc),
    )


def _safe_error_message(exc: Exception) -> str:
    """A concise, external-only description -- no stack trace, no secrets, no
    hidden reasoning."""
    message = str(exc).strip() or type(exc).__name__
    max_len = 500
    if len(message) > max_len:
        message = message[:max_len] + "... (truncated)"
    return message
