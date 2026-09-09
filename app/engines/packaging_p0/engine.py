"""PackagingP0Engine: tests whether the approved narrative can be expressed as
a strong, honest viewer promise before script writing -- sixth vertical slice.

PACKAGING_P0 -> load+verify IdeaCandidate/ResearchPackage/NarrativePlan ->
generate_structured(PackagingPrototype) -> business validation (non-blank
fields only -- risk_of_misleading is never a validation failure) -> one
bounded correction attempt if needed -> persist artifact -> update
Project.packaging_prototype_id -> persist ModuleRun.

IMPORTANT: this engine never transitions project state, even for a
HIGH-risk prototype. The project stays in PACKAGING_P0; the prototype
awaits an explicit human decision (see app/review/service.py). No Script
Engine, no orchestrator, no BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.errors import (
    MissingIdeaArtifactError,
    MissingNarrativePlanArtifactError,
    MissingResearchPackageArtifactError,
    PackagingBusinessValidationError,
)
from app.engines.packaging_p0.models import (
    PACKAGING_PROTOTYPE_ARTIFACT_TYPE,
    PackagingP0Input,
    PackagingP0Result,
)
from app.engines.packaging_p0.prompt import (
    build_correction_request,
    build_system_prompt,
    build_user_prompt,
)
from app.engines.packaging_p0.validation import validate_packaging_prototype
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.idea import IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import ResearchPackage
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "packaging_p0_engine"
MODULE_VERSION = "0.1"

# "One bounded correction attempt" -- exactly one extra provider call, with no
# further JSON/schema retries of its own, if business validation fails.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class PackagingP0Engine:
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

    def run(self, engine_input: PackagingP0Input) -> PackagingP0Result:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.PACKAGING_P0:
            raise EngineStateError(
                f"PackagingP0Engine requires project state PACKAGING_P0, got {project.state.value}"
            )
        idea = self._load_and_verify_idea(project)
        research_package = self._load_and_verify_research_package(project)
        narrative_plan = self._load_and_verify_narrative_plan(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(idea.id), str(research_package.id), str(narrative_plan.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            prototype, generation_attempts, response = self._generate_prototype(
                idea, research_package, narrative_plan, engine_input.additional_context
            )

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, prototype
            )
            self._project_repo.update_artifact_reference(
                self._db_engine, engine_input.project_id, "packaging_prototype_id", prototype.id
            )
            # No state transition, even for a HIGH-risk prototype: the project
            # stays in PACKAGING_P0 awaiting an explicit human decision.
        except Exception as exc:
            # Broad on purpose: any failure from generation through persistence
            # must be recorded as a FAILED audit record and re-raised unchanged
            # -- mirrors every prior engine.
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, prototype.id)
        )

        return PackagingP0Result(
            packaging=prototype,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
        )

    def _load_and_verify_idea(self, project: Project) -> IdeaCandidate:
        if project.idea_candidate_id is None:
            raise MissingIdeaArtifactError(
                f"Project {project.project_id} has no idea_candidate_id reference"
            )
        try:
            idea = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingIdeaArtifactError(
                f"Project {project.project_id} idea_candidate_id references a "
                f"missing or invalid IdeaCandidate artifact"
            ) from exc
        if idea.id != project.idea_candidate_id:
            raise MissingIdeaArtifactError(
                f"Stored IdeaCandidate id does not match project.idea_candidate_id "
                f"for project {project.project_id}"
            )
        return idea

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

    def _generate_prototype(
        self,
        idea: IdeaCandidate,
        research_package: ResearchPackage,
        narrative_plan: NarrativePlan,
        additional_context: str | None,
    ):
        request = LLMRequest(
            system_prompt=build_system_prompt(self._global_config, idea, research_package, narrative_plan),
            user_prompt=build_user_prompt(additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            PackagingPrototype,
            max_retries=self._llm_settings.max_structured_retries,
        )
        prototype = generation.value
        attempts = generation.attempts
        response = generation.response

        issues = validate_packaging_prototype(prototype)
        if issues:
            correction_request = build_correction_request(request, issues)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                PackagingPrototype,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            prototype = correction_generation.value
            attempts += correction_generation.attempts
            response = correction_generation.response

            issues = validate_packaging_prototype(prototype)
            if issues:
                raise PackagingBusinessValidationError(issues)

        return prototype, attempts, response


def _with_success(run_record: ModuleRun, prototype_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(prototype_id),
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
