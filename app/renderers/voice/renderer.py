"""VoiceRenderer: turns a locked VoicePlan into real rendered audio files --
the first execution/rendering component, and the first real filesystem I/O
in the runtime path.

MVP_COMPLETE -> load+verify ScriptPlan -> load+verify VoicePlan -> verify
VoicePlan freshness against the current ScriptPlan -> for every VoiceChunk,
for every take, synthesize audio via a TTSProvider (retrying only a
provider-side failure, up to a bounded number of attempts) -> write the
returned bytes to disk via AudioFileStore (no retry on a write failure) ->
build a VoiceRenderManifest -> validate its own integrity as defense in
depth -> persist the manifest artifact -> persist ModuleRun.

IMPORTANT: this package has ZERO app.llm dependency, direct or transitive --
there is no prompt here and nothing here reasons about content. The two
upstream artifact-type constants below are deliberately duplicated rather
than imported from app.engines.script.models / app.engines.voice_plan.models,
because importing either would transitively pull in app.llm.models (see the
comment at their definition). Like every engine, this renderer is
artifact-driven, not workflow-driven: it never transitions project state
(MVP_COMPLETE is unchanged before and after it runs) and never updates any
Project reference field (no voice_render_manifest_id field exists on
Project -- confirmed absent, not added this phase). It also never re-saves
or modifies the ScriptPlan or VoicePlan it renders. No real TTS vendor
integration, no music/SFX rendering, no Visual Renderer, no orchestrator.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.audio.config import TTSSettings
from app.audio.errors import TTSProviderError
from app.audio.models import TTSRequest, TTSResponse
from app.audio.provider import TTSProvider
from app.audio.storage import AudioFileStore
from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.common import AudioFormat, ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.voice.errors import (
    MissingScriptPlanArtifactError,
    MissingVoicePlanArtifactError,
    RendererStateError,
    StaleVoicePlanError,
    VoiceRenderManifestIntegrityError,
)
from app.renderers.voice.models import (
    VOICE_RENDER_MANIFEST_ARTIFACT_TYPE,
    VoiceRendererInput,
    VoiceRendererResult,
)
from app.renderers.voice.validation import validate_voice_render_manifest
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

# Duplicated from app.engines.script.models.SCRIPT_PLAN_ARTIFACT_TYPE and
# app.engines.voice_plan.models.VOICE_PLAN_ARTIFACT_TYPE rather than
# imported: both of those modules import app.llm.models (for their own
# TokenUsage-carrying *Result types), and this package must stay free of
# app.llm even transitively. These string values are load-bearing keys into
# generic artifact storage, not engine reasoning logic.
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"

MODULE_NAME = "voice_renderer"
MODULE_VERSION = "0.1"


class VoiceRenderer:
    def __init__(
        self,
        db_engine: Engine,
        tts_provider: TTSProvider,
        tts_settings: TTSSettings,
        audio_store: AudioFileStore,
        *,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._tts_provider = tts_provider
        self._tts_settings = tts_settings
        self._audio_store = audio_store
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, renderer_input: VoiceRendererInput) -> VoiceRendererResult:
        project = self._project_repo.get_project(self._db_engine, renderer_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"VoiceRenderer requires project state MVP_COMPLETE, got {project.state.value}"
            )
        script_plan = self._load_and_verify_script_plan(project)
        voice_plan = self._load_and_verify_voice_plan(project)
        self._verify_voice_plan_is_fresh(project, voice_plan)

        run_record = ModuleRun(
            project_id=renderer_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(script_plan.id), str(voice_plan.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            manifest, provider_call_count = self._render_all(script_plan, voice_plan)

            issues = validate_voice_render_manifest(manifest, script_plan, voice_plan)
            if issues:
                raise VoiceRenderManifestIntegrityError(issues)

            self._artifact_repo.save_artifact(
                self._db_engine,
                renderer_input.project_id,
                VOICE_RENDER_MANIFEST_ARTIFACT_TYPE,
                manifest,
            )
            # No Project reference update: no voice_render_manifest_id field
            # exists on Project (confirmed absent, Phase 17 compat finding).
            # No state transition: the project stays in MVP_COMPLETE -- this
            # renderer is artifact-driven, not workflow-driven.
        except Exception as exc:
            # Broad on purpose: any failure from rendering through
            # persistence must be recorded as a FAILED audit record and
            # re-raised unchanged -- mirrors every prior engine.
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, manifest.id)
        )

        return VoiceRendererResult(
            manifest=manifest,
            module_run_id=run_record.run_id,
            provider_call_count=provider_call_count,
            rendered_take_count=len(manifest.renders),
            total_duration_seconds=_sum_durations_or_none(manifest.renders),
        )

    def _load_and_verify_script_plan(self, project: Project) -> ScriptPlan:
        if project.script_plan_id is None:
            raise MissingScriptPlanArtifactError(
                f"Project {project.project_id} has no script_plan_id reference"
            )
        try:
            script_plan = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan
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

    def _load_and_verify_voice_plan(self, project: Project) -> VoicePlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _VOICE_PLAN_ARTIFACT_TYPE, VoicePlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVoicePlanArtifactError(
                f"Project {project.project_id} has no valid VoicePlan artifact"
            ) from exc

    def _verify_voice_plan_is_fresh(self, project: Project, voice_plan: VoicePlan) -> None:
        """A real production-integrity gate: no provider call, no ModuleRun,
        no file write happens before this check. VoicePlan must have been
        generated for the ScriptPlan the project currently references."""
        if voice_plan.script_plan_id != project.script_plan_id:
            raise StaleVoicePlanError(
                f"The current VoicePlan for project {project.project_id} was "
                f"generated for ScriptPlan {voice_plan.script_plan_id}, but the "
                f"project currently references ScriptPlan "
                f"{project.script_plan_id}; rerun Voice Planning first"
            )

    def _render_all(
        self, script_plan: ScriptPlan, voice_plan: VoicePlan
    ) -> tuple[VoiceRenderManifest, int]:
        line_text_by_id = {
            line.line_id: line.text for beat in script_plan.beats for line in beat.lines
        }

        renders: list[RenderedVoiceTake] = []
        provider_call_count = 0
        first_response_provider: str | None = None

        for chunk in voice_plan.chunks:
            chunk_text = "\n".join(line_text_by_id[line_id] for line_id in chunk.line_ids)

            for take_number in range(1, chunk.take_count + 1):
                render_job_id = f"{chunk.chunk_id}_T{take_number}"
                relative_path = _relative_path_for(render_job_id, self._tts_settings.output_format)

                request = TTSRequest(
                    text=chunk_text,
                    voice_id=self._tts_settings.voice_id,
                    voice_state=chunk.voice_state,
                    pace=chunk.pace,
                    energy=chunk.energy,
                    output_format=self._tts_settings.output_format,
                    metadata={
                        "chunk_id": chunk.chunk_id,
                        "take_number": str(take_number),
                        "render_job_id": render_job_id,
                    },
                )

                response, calls_made = self._synthesize_with_retry(request)
                provider_call_count += calls_made
                if first_response_provider is None:
                    first_response_provider = response.provider

                # No retry on a write failure: a provider call that already
                # succeeded is not repeated just because the disk write
                # after it failed.
                self._audio_store.write(relative_path, response.audio_bytes)

                renders.append(_rendered_take(chunk, take_number, render_job_id, relative_path, response))

        manifest = VoiceRenderManifest(
            script_plan_id=script_plan.id,
            voice_plan_id=voice_plan.id,
            provider=first_response_provider,
            voice_id=self._tts_settings.voice_id,
            output_format=self._tts_settings.output_format,
            renders=renders,
            created_at=datetime.now(timezone.utc),
        )
        return manifest, provider_call_count

    def _synthesize_with_retry(self, request: TTSRequest) -> tuple[TTSResponse, int]:
        """At most 1 + max_provider_retries total attempts. Only
        TTSProviderError is retried -- a TTSOutputError or any other
        exception propagates immediately, unretried."""
        total_attempts = 1 + self._tts_settings.max_provider_retries

        for attempt in range(1, total_attempts + 1):
            try:
                response = self._tts_provider.synthesize(request)
            except TTSProviderError:
                if attempt >= total_attempts:
                    raise
                continue
            return response, attempt

        raise AssertionError("unreachable: _synthesize_with_retry loop exited without a result")


def _relative_path_for(render_job_id: str, output_format: AudioFormat) -> str:
    return f"{render_job_id}.{output_format.value.lower()}"


def _rendered_take(
    chunk: VoiceChunk,
    take_number: int,
    render_job_id: str,
    relative_path: str,
    response: TTSResponse,
) -> RenderedVoiceTake:
    return RenderedVoiceTake(
        render_job_id=render_job_id,
        chunk_id=chunk.chunk_id,
        take_number=take_number,
        line_ids=list(chunk.line_ids),
        file_path=relative_path,
        duration_seconds=response.duration_seconds,
        provider_request_id=response.provider_request_id,
        music_state=chunk.music_state,
        sfx_opportunity=chunk.sfx_opportunity,
    )


def _sum_durations_or_none(renders: list[RenderedVoiceTake]) -> float | None:
    """Do not guess: if any take's duration is unknown, the total is unknown."""
    durations = [render.duration_seconds for render in renders]
    if any(duration is None for duration in durations):
        return None
    return sum(durations)


def _with_success(run_record: ModuleRun, manifest_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(manifest_id),
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
