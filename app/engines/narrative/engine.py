"""NarrativeEngine: transforms validated research into investigative story
architecture -- fifth vertical slice.

NARRATIVE -> load+verify IdeaCandidate/ResearchPackage -> generate_structured
(NarrativePlan) -> deterministic central_question override -> business
validation (question-ladder structure, claim referential integrity) -> one
bounded correction attempt if needed -> persist artifact -> update
Project.narrative_plan_id -> transition NARRATIVE -> NARRATIVE_REVIEW ->
persist ModuleRun.

No Packaging/Script engine, no orchestrator, no BaseEngine framework. Human
narrative-approve routing (NARRATIVE_REVIEW -> SCRIPT) is intentionally not
implemented here -- see app/review/service.py's approve_narrative and
docs/TECHNICAL_SPEC_v0.1.md, Phase 8.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.errors import (
    MissingIdeaArtifactError,
    MissingResearchPackageArtifactError,
    NarrativeBusinessValidationError,
)
from app.engines.narrative.models import (
    NARRATIVE_PLAN_ARTIFACT_TYPE,
    NarrativeEngineInput,
    NarrativeEngineResult,
)
from app.engines.narrative.prompt import build_correction_request, build_system_prompt, build_user_prompt
from app.engines.narrative.validation import validate_narrative_plan
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.idea import IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import NarrativePlan
from app.models.project import Project
from app.models.research import ResearchPackage
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "narrative_engine"
MODULE_VERSION = "0.1"

# "Maximum business correction attempts: 1" -- exactly one extra provider call,
# with no further JSON/schema retries of its own, if business validation fails.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class NarrativeEngine:
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

    def run(self, engine_input: NarrativeEngineInput) -> NarrativeEngineResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.NARRATIVE:
            raise EngineStateError(
                f"NarrativeEngine requires project state NARRATIVE, got {project.state.value}"
            )
        idea = self._load_and_verify_idea(project)
        research_package = self._load_and_verify_research_package(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(idea.id), str(research_package.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            plan, generation_attempts, response, business_correction_used = self._generate_plan(
                idea, research_package, engine_input.additional_context
            )

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, plan
            )
            self._project_repo.update_artifact_reference(
                self._db_engine, engine_input.project_id, "narrative_plan_id", plan.id
            )
            self._project_repo.update_project_state(
                self._db_engine, engine_input.project_id, ProjectState.NARRATIVE_REVIEW
            )
        except Exception as exc:
            # Broad on purpose: any failure from generation through the state
            # transition must be recorded as a FAILED audit record and
            # re-raised unchanged -- mirrors every prior engine.
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, plan.id)
        )

        return NarrativeEngineResult(
            narrative=plan,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
            business_correction_used=business_correction_used,
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

    def _generate_plan(
        self, idea: IdeaCandidate, research_package: ResearchPackage, additional_context: str | None
    ):
        request = LLMRequest(
            system_prompt=build_system_prompt(self._global_config, idea, research_package),
            user_prompt=build_user_prompt(additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            NarrativePlan,
            max_retries=self._llm_settings.max_structured_retries,
        )
        plan = generation.value.model_copy(update={"central_question": idea.central_question})
        attempts = generation.attempts
        response = generation.response
        business_correction_used = False

        issues = validate_narrative_plan(plan, research_package)
        if issues:
            business_correction_used = True
            correction_request = build_correction_request(request, issues)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                NarrativePlan,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            plan = correction_generation.value.model_copy(
                update={"central_question": idea.central_question}
            )
            attempts += correction_generation.attempts
            response = correction_generation.response

            issues = validate_narrative_plan(plan, research_package)
            if issues:
                raise NarrativeBusinessValidationError(issues)

        return plan, attempts, response, business_correction_used


def _with_success(run_record: ModuleRun, plan_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(plan_id),
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
