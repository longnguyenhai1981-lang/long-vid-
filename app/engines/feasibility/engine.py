"""FeasibilityEngine: evaluates an approved idea + R0 evidence -- third vertical slice.

FEASIBILITY -> generate_structured(FeasibilityReport) -> deterministically
derive overall status from the five axes -> persist artifact -> update
Project.feasibility_id -> persist ModuleRun.

IMPORTANT: this engine never transitions project state. The project stays
in FEASIBILITY; the report awaits an explicit human decision (see
app/review/service.py's decide_feasibility). No orchestrator, no
BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.engines.errors import EngineStateError
from app.engines.feasibility.errors import MissingIdeaArtifactError, MissingResearchArtifactError
from app.engines.feasibility.models import (
    FEASIBILITY_REPORT_ARTIFACT_TYPE,
    FeasibilityEngineInput,
    FeasibilityEngineResult,
)
from app.engines.feasibility.prompt import build_system_prompt, build_user_prompt
from app.engines.feasibility.status import derive_overall_status
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.config.loader import GlobalConfig
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.research import ResearchR0
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "feasibility_engine"
MODULE_VERSION = "0.1"


class FeasibilityEngine:
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

    def run(self, engine_input: FeasibilityEngineInput) -> FeasibilityEngineResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.FEASIBILITY:
            raise EngineStateError(
                f"FeasibilityEngine requires project state FEASIBILITY, got {project.state.value}"
            )
        idea = self._load_and_verify_idea(project)
        research = self._load_and_verify_research(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(idea.id), str(research.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            report, generation_attempts, response = self._generate_report(
                idea, research, engine_input.additional_context
            )

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, report
            )
            self._project_repo.update_artifact_reference(
                self._db_engine, engine_input.project_id, "feasibility_id", report.id
            )
            # No state transition: the project stays in FEASIBILITY awaiting an
            # explicit human decision (app.review.service.decide_feasibility).
        except Exception as exc:
            # Broad on purpose: any failure from generation through persistence
            # must be recorded as a FAILED audit record and re-raised unchanged
            # -- mirrors IdeaEngine (Phase 4) and R0ResearchEngine (Phase 5).
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, report.id)
        )

        return FeasibilityEngineResult(
            feasibility=report,
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

    def _load_and_verify_research(self, project: Project) -> ResearchR0:
        if project.research_r0_id is None:
            raise MissingResearchArtifactError(
                f"Project {project.project_id} has no research_r0_id reference"
            )
        try:
            research = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingResearchArtifactError(
                f"Project {project.project_id} research_r0_id references a "
                f"missing or invalid ResearchR0 artifact"
            ) from exc
        if research.id != project.research_r0_id:
            raise MissingResearchArtifactError(
                f"Stored ResearchR0 id does not match project.research_r0_id "
                f"for project {project.project_id}"
            )
        return research

    def _generate_report(self, idea: IdeaCandidate, research: ResearchR0, additional_context: str | None):
        request = LLMRequest(
            system_prompt=build_system_prompt(self._global_config),
            user_prompt=build_user_prompt(idea, research, additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            FeasibilityReport,
            max_retries=self._llm_settings.max_structured_retries,
        )
        report = generation.value.model_copy(
            update={"status": derive_overall_status(generation.value)}
        )
        return report, generation.attempts, generation.response


def _with_success(run_record: ModuleRun, report_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(report_id),
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
