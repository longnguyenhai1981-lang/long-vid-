"""IdeaEngine: the first real content engine -- a vertical slice, not a framework.

IDEA_DISCOVERY -> build prompt -> generate_structured -> IdeaCandidate ->
persist artifact -> update Project.idea_candidate_id -> IDEA_DISCOVERY ->
IDEA_REVIEW -> persist ModuleRun.

No orchestrator, no research/narrative/script engine, no BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.idea.models import (
    IDEA_CANDIDATE_ARTIFACT_TYPE,
    IdeaEngineInput,
    IdeaEngineResult,
)
from app.engines.idea.prompt import build_system_prompt, build_user_prompt
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.idea import IdeaCandidate
from app.models.module_run import ModuleRun
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage

MODULE_NAME = "idea_engine"
MODULE_VERSION = "0.1"


class IdeaEngine:
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

    def run(self, engine_input: IdeaEngineInput) -> IdeaEngineResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.IDEA_DISCOVERY:
            raise EngineStateError(
                f"IdeaEngine requires project state IDEA_DISCOVERY, got {project.state.value}"
            )

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(engine_input.project_id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            idea, generation_attempts, response = self._generate_idea(engine_input)

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea
            )
            self._project_repo.update_artifact_reference(
                self._db_engine, engine_input.project_id, "idea_candidate_id", idea.id
            )
            self._project_repo.update_project_state(
                self._db_engine, engine_input.project_id, ProjectState.IDEA_REVIEW
            )
        except Exception as exc:
            # Broad on purpose: ANY failure from generation through the state
            # transition must be recorded as a FAILED audit record and re-raised
            # unchanged -- see docs/TECHNICAL_SPEC_v0.1.md, Phase 4.
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, idea.id)
        )

        return IdeaEngineResult(
            idea=idea,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
        )

    def _generate_idea(self, engine_input: IdeaEngineInput):
        request = LLMRequest(
            system_prompt=build_system_prompt(self._global_config),
            user_prompt=build_user_prompt(engine_input),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            IdeaCandidate,
            max_retries=self._llm_settings.max_structured_retries,
        )
        return generation.value, generation.attempts, generation.response


def _with_success(run_record: ModuleRun, idea_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(idea_id),
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
