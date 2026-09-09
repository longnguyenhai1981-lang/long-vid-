"""PackagingP1Engine: the final title/thumbnail packaging pass, made after
the script, voice, visual, and assembly plans are all known -- the twelfth
engine, and the fourth in the production layer.

MVP_COMPLETE -> load+verify ScriptPlan -> load+verify VoicePlan -> verify
VoicePlan freshness against the current ScriptPlan -> load+verify
VisualPlan -> verify VisualPlan freshness against the current ScriptPlan
AND VoicePlan -> load+verify AssemblyPlan -> verify AssemblyPlan freshness
against the current ScriptPlan, VoicePlan, AND VisualPlan -> load+verify
the currently-referenced PackagingPrototype, NarrativePlan, and
ResearchPackage (four hard production-integrity gates plus three standard
reference checks, all before any LLM call, ModuleRun, or write) ->
generate_structured(FinalPackagingPlan) -> deterministic normalization
(the four upstream-reference ids) -> business validation (every required
field non-blank) -> one bounded correction attempt if needed -> persist
artifact -> persist ModuleRun.

IMPORTANT: this engine is artifact-driven, not workflow-driven. It never
transitions project state (MVP_COMPLETE is the terminal core-MVP state; it
stays MVP_COMPLETE before and after this engine runs), and it never updates
any Project reference field (no packaging_p1_id field exists on Project --
confirmed absent, not added this phase). It also never re-saves or
modifies PackagingPrototype, ResearchPackage, NarrativePlan, ScriptPlan,
VoicePlan, VisualPlan, or AssemblyPlan. A HIGH-risk FinalPackagingPlan is
still persisted -- exactly like Packaging P0's HIGH-risk
PackagingPrototype -- generation never auto-fails on risk level alone. No
thumbnail rendering, no image-prompt generation, no publishing, no
orchestrator, no BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.packaging_p1.errors import (
    MissingAssemblyPlanArtifactError,
    MissingNarrativePlanArtifactError,
    MissingPackagingPrototypeArtifactError,
    MissingResearchPackageArtifactError,
    MissingScriptPlanArtifactError,
    MissingVisualPlanArtifactError,
    MissingVoicePlanArtifactError,
    PackagingP1BusinessValidationError,
    StaleAssemblyPlanError,
    StaleVisualPlanError,
    StaleVoicePlanError,
)
from app.engines.packaging_p1.models import (
    PACKAGING_P1_ARTIFACT_TYPE,
    PackagingP1Input,
    PackagingP1Result,
)
from app.engines.packaging_p1.prompt import (
    build_correction_request,
    build_system_prompt,
    build_user_prompt,
)
from app.engines.packaging_p1.validation import (
    normalize_final_packaging_plan,
    validate_final_packaging_plan,
)
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.assembly import AssemblyPlan
from app.models.common import ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.project import Project
from app.models.research import ResearchPackage
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "packaging_p1_engine"
MODULE_VERSION = "0.1"

# "One bounded correction attempt" -- exactly one extra provider call, with no
# further JSON/schema retries of its own, if business validation fails.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class PackagingP1Engine:
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

    def run(self, engine_input: PackagingP1Input) -> PackagingP1Result:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise EngineStateError(
                f"PackagingP1Engine requires project state MVP_COMPLETE, got {project.state.value}"
            )
        script_plan = self._load_and_verify_script_plan(project)
        voice_plan = self._load_and_verify_voice_plan(project)
        self._verify_voice_plan_is_fresh(project, voice_plan)
        visual_plan = self._load_and_verify_visual_plan(project)
        self._verify_visual_plan_is_fresh(project, voice_plan, visual_plan)
        assembly_plan = self._load_and_verify_assembly_plan(project)
        self._verify_assembly_plan_is_fresh(project, voice_plan, visual_plan, assembly_plan)
        packaging_prototype = self._load_and_verify_packaging_prototype(project)
        narrative_plan = self._load_and_verify_narrative_plan(project)
        research_package = self._load_and_verify_research_package(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[
                str(packaging_prototype.id),
                str(script_plan.id),
                str(visual_plan.id),
                str(assembly_plan.id),
                str(research_package.id),
            ],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            final_packaging_plan, generation_attempts, response, business_correction_used = (
                self._generate_final_packaging_plan(
                    packaging_prototype,
                    research_package,
                    narrative_plan,
                    script_plan,
                    visual_plan,
                    assembly_plan,
                    engine_input.additional_context,
                )
            )

            self._artifact_repo.save_artifact(
                self._db_engine,
                engine_input.project_id,
                PACKAGING_P1_ARTIFACT_TYPE,
                final_packaging_plan,
            )
            # No Project reference update: no packaging_p1_id field exists
            # on Project (confirmed absent, Phase 16 compat finding 2).
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
            self._db_engine, _with_success(run_record, final_packaging_plan.id)
        )

        return PackagingP1Result(
            final_packaging_plan=final_packaging_plan,
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

    def _load_and_verify_voice_plan(self, project: Project) -> VoicePlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVoicePlanArtifactError(
                f"Project {project.project_id} has no valid VoicePlan artifact"
            ) from exc

    def _verify_voice_plan_is_fresh(self, project: Project, voice_plan: VoicePlan) -> None:
        if voice_plan.script_plan_id != project.script_plan_id:
            raise StaleVoicePlanError(
                f"The current VoicePlan for project {project.project_id} was "
                f"generated for ScriptPlan {voice_plan.script_plan_id}, but "
                f"the project currently references ScriptPlan "
                f"{project.script_plan_id}; rerun Voice Planning first"
            )

    def _load_and_verify_visual_plan(self, project: Project) -> VisualPlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVisualPlanArtifactError(
                f"Project {project.project_id} has no valid VisualPlan artifact"
            ) from exc

    def _verify_visual_plan_is_fresh(
        self, project: Project, voice_plan: VoicePlan, visual_plan: VisualPlan
    ) -> None:
        if visual_plan.script_plan_id != project.script_plan_id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was "
                f"generated for ScriptPlan {visual_plan.script_plan_id}, but "
                f"the project currently references ScriptPlan "
                f"{project.script_plan_id}; rerun Visual Planning first"
            )
        if visual_plan.voice_plan_id != voice_plan.id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was "
                f"generated for VoicePlan {visual_plan.voice_plan_id}, but "
                f"the current VoicePlan is {voice_plan.id}; rerun Visual "
                f"Planning first"
            )

    def _load_and_verify_assembly_plan(self, project: Project) -> AssemblyPlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingAssemblyPlanArtifactError(
                f"Project {project.project_id} has no valid AssemblyPlan artifact"
            ) from exc

    def _verify_assembly_plan_is_fresh(
        self,
        project: Project,
        voice_plan: VoicePlan,
        visual_plan: VisualPlan,
        assembly_plan: AssemblyPlan,
    ) -> None:
        if assembly_plan.script_plan_id != project.script_plan_id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was "
                f"generated for ScriptPlan {assembly_plan.script_plan_id}, but "
                f"the project currently references ScriptPlan "
                f"{project.script_plan_id}; rerun Timing/Assembly Planning first"
            )
        if assembly_plan.voice_plan_id != voice_plan.id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was "
                f"generated for VoicePlan {assembly_plan.voice_plan_id}, but "
                f"the current VoicePlan is {voice_plan.id}; rerun Timing/"
                f"Assembly Planning first"
            )
        if assembly_plan.visual_plan_id != visual_plan.id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was "
                f"generated for VisualPlan {assembly_plan.visual_plan_id}, but "
                f"the current VisualPlan is {visual_plan.id}; rerun Timing/"
                f"Assembly Planning first"
            )

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

    def _generate_final_packaging_plan(
        self,
        packaging_prototype: PackagingPrototype,
        research_package: ResearchPackage,
        narrative_plan: NarrativePlan,
        script_plan: ScriptPlan,
        visual_plan: VisualPlan,
        assembly_plan: AssemblyPlan,
        additional_context: str | None,
    ):
        request = LLMRequest(
            system_prompt=build_system_prompt(
                self._global_config,
                packaging_prototype,
                research_package,
                narrative_plan,
                script_plan,
                visual_plan,
                assembly_plan,
            ),
            user_prompt=build_user_prompt(additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            FinalPackagingPlan,
            max_retries=self._llm_settings.max_structured_retries,
        )
        final_packaging_plan = normalize_final_packaging_plan(
            generation.value, packaging_prototype, script_plan, visual_plan, assembly_plan
        )
        attempts = generation.attempts
        response = generation.response
        business_correction_used = False

        issues = validate_final_packaging_plan(final_packaging_plan)
        if issues:
            business_correction_used = True
            correction_request = build_correction_request(request, issues)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                FinalPackagingPlan,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            final_packaging_plan = normalize_final_packaging_plan(
                correction_generation.value, packaging_prototype, script_plan, visual_plan, assembly_plan
            )
            attempts += correction_generation.attempts
            response = correction_generation.response

            issues = validate_final_packaging_plan(final_packaging_plan)
            if issues:
                raise PackagingP1BusinessValidationError(issues)

        return final_packaging_plan, attempts, response, business_correction_used


def _with_success(run_record: ModuleRun, final_packaging_plan_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(final_packaging_plan_id),
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
