"""VoicePlanningEngine: converts the locked ScriptPlan into delivery
performance metadata -- the first production-layer engine, ninth overall.

MVP_COMPLETE -> load+verify ScriptPlan -> generate_structured(VoicePlan) ->
business validation (line coverage/order/adjacency against the current
ScriptPlan) -> one bounded correction attempt if needed -> persist artifact
-> persist ModuleRun.

IMPORTANT: this engine is artifact-driven, not workflow-driven. It never
transitions project state (MVP_COMPLETE is the terminal core-MVP state; it
stays MVP_COMPLETE before and after this engine runs), and it never updates
any Project reference field (no voice_plan_id field exists on Project --
confirmed absent, not added this phase). It also never re-saves or modifies
the ScriptPlan it plans delivery for. No real TTS/audio, no Visual Engine,
no Timing/Assembly, no orchestrator, no BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.errors import (
    MissingScriptPlanArtifactError,
    VoicePlanBusinessValidationError,
)
from app.engines.voice_plan.models import (
    VOICE_PLAN_ARTIFACT_TYPE,
    VoicePlanningInput,
    VoicePlanningResult,
)
from app.engines.voice_plan.prompt import build_correction_request, build_system_prompt, build_user_prompt
from app.engines.voice_plan.validation import validate_voice_plan
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.voice import VoicePlan
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "voice_plan_engine"
MODULE_VERSION = "0.1"

# "One bounded correction attempt" -- exactly one extra provider call, with no
# further JSON/schema retries of its own, if business validation fails.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class VoicePlanningEngine:
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

    def run(self, engine_input: VoicePlanningInput) -> VoicePlanningResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise EngineStateError(
                f"VoicePlanningEngine requires project state MVP_COMPLETE, got {project.state.value}"
            )
        script_plan = self._load_and_verify_script_plan(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(script_plan.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            voice_plan, generation_attempts, response, business_correction_used = (
                self._generate_voice_plan(script_plan, engine_input.additional_context)
            )

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan
            )
            # No Project reference update: no voice_plan_id field exists on
            # Project (confirmed absent, Phase 13 compat finding 3).
            # No state transition: the project stays in MVP_COMPLETE --
            # this engine is artifact-driven, not workflow-driven.
        except Exception as exc:
            # Broad on purpose: any failure from generation through
            # persistence must be recorded as a FAILED audit record and
            # re-raised unchanged -- mirrors every prior engine.
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, voice_plan.id)
        )

        return VoicePlanningResult(
            voice_plan=voice_plan,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
            business_correction_used=business_correction_used,
        )

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

    def _generate_voice_plan(self, script_plan: ScriptPlan, additional_context: str | None):
        request = LLMRequest(
            system_prompt=build_system_prompt(self._global_config, script_plan),
            user_prompt=build_user_prompt(script_plan, additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            VoicePlan,
            max_retries=self._llm_settings.max_structured_retries,
        )
        voice_plan = generation.value
        attempts = generation.attempts
        response = generation.response
        business_correction_used = False

        issues = validate_voice_plan(voice_plan, script_plan)
        if issues:
            business_correction_used = True
            correction_request = build_correction_request(request, issues)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                VoicePlan,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            voice_plan = correction_generation.value
            attempts += correction_generation.attempts
            response = correction_generation.response

            issues = validate_voice_plan(voice_plan, script_plan)
            if issues:
                raise VoicePlanBusinessValidationError(issues)

        return voice_plan, attempts, response, business_correction_used


def _with_success(run_record: ModuleRun, voice_plan_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(voice_plan_id),
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
