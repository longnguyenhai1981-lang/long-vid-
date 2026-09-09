"""Real Phase 34 adapters for the twelve upstream creative/planning nodes
(IDEA through PACKAGING_P1).

This module lives OUTSIDE app/orchestration/ specifically because every
one of these twelve engines imports app.llm (LLMProvider/LLMSettings/
generate_structured) -- confirmed by reading each engine.py directly
before writing this file, never assumed from phase names. Keeping that
import here, and never inside app/orchestration/, is the entire reason
this package exists (requirement #2): the orchestrator core stays
provider-unaware; this bridge is where "execute the real engine" lives.

Every adapter below implements the SAME Phase 33 NodeAdapter contract
(load_current/is_fresh/execute/gate_ok) unchanged -- no parallel runner
API, no new adapter shape (requirement #5). Each execute() does nothing
but resolve inputs from ExecutionContext and call the existing engine's
own public run() method; no prompt construction, no business validation,
no re-derivation of an engine's own decision logic lives here
(requirement #8).

Freshness (requirement #9): five of these twelve artifact models declare
NO upstream foreign-key field at all (FeasibilityReport, ResearchPackage,
NarrativePlan, ScriptPlan, ScriptVerificationReport -- confirmed by
reading each model directly) -- app/review/service.py's own
_verify_packaging_is_fresh/_verify_script_verification_is_fresh solve
this the same way this module does for all five: compare the latest
SUCCESS ModuleRun's own recorded input_ids against the CURRENT upstream
artifact ids (never "latest artifact exists", requirement #9's explicit
ban). The other seven (IdeaCandidate has no upstream; ResearchR0 has
idea_id; VoicePlan/VisualPlan/AssemblyPlan/FinalPackagingPlan carry their
own real foreign keys) compare those fields directly, exactly like
Phase 33's own downstream adapters.
"""

from __future__ import annotations

from app.config.loader import GlobalConfig, load_global_config
from app.engines.assembly_plan.engine import AssemblyPlanningEngine
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlanningInput
from app.engines.feasibility.engine import FeasibilityEngine
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityEngineInput
from app.engines.idea.engine import IdeaEngine
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.engine import NarrativeEngine
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativeEngineInput
from app.engines.packaging_p0.engine import PackagingP0Engine
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingP0Input
from app.engines.packaging_p1.engine import PackagingP1Engine
from app.engines.packaging_p1.models import PACKAGING_P1_ARTIFACT_TYPE, PackagingP1Input
from app.engines.research_r0.engine import R0ResearchEngine
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE, R0ResearchInput
from app.engines.research_r1.engine import R1ResearchEngine
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE, R1ResearchInput
from app.engines.script.engine import ScriptEngine
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE, ScriptEngineInput
from app.engines.script_verification.engine import ScriptVerificationEngine
from app.engines.script_verification.models import (
    SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
    ScriptVerificationInput,
)
from app.engines.visual_plan.engine import VisualPlanningEngine
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE, VisualPlanningInput
from app.engines.voice_plan.engine import VoicePlanningEngine
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE, VoicePlanningInput
from app.models.assembly import AssemblyPlan
from app.models.common import GateStatus, ModuleRunStatus, RiskLevel
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.research import ResearchPackage, ResearchR0
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.orchestration.models import BlockedReason
from app.orchestration.registry import ExecutionContext, NodeExecutionResult
from app.production_adapters.errors import AdapterConfigurationError, ProductionInputMissingError
from app.storage.artifacts import get_artifact
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.storage.module_runs import list_module_runs_for_project


def _load_optional(engine, project_id, artifact_type, model_class):
    try:
        return get_artifact(engine, project_id, artifact_type, model_class)
    except (ArtifactNotFoundError, ArtifactValidationError):
        return None


def _latest_successful_module_run(engine, project_id, module: str):
    matches = [
        run
        for run in list_module_runs_for_project(engine, project_id)
        if run.module == module and run.status is ModuleRunStatus.SUCCESS
    ]
    return matches[-1] if matches else None


def _module_run_input_ids_match(engine, project_id, module: str, expected_ids: list) -> bool:
    """Freshness for the five artifact models with no upstream foreign
    key of their own -- mirrors app/review/service.py's own
    _verify_packaging_is_fresh/_verify_script_verification_is_fresh
    exactly: the latest SUCCESS ModuleRun's own recorded input_ids is the
    only durable record of what an artifact was actually generated from."""
    if any(value is None for value in expected_ids):
        return False
    run = _latest_successful_module_run(engine, project_id, module)
    if run is None:
        return False
    return run.input_ids == [str(value) for value in expected_ids]


def _require_llm_context(ctx: ExecutionContext) -> tuple:
    if ctx.llm_provider is None or ctx.llm_settings is None:
        raise AdapterConfigurationError(
            "ExecutionContext.llm_provider/llm_settings must be set to execute an upstream "
            "creative-planning node."
        )
    global_config = ctx.global_config or load_global_config()
    return ctx.llm_provider, global_config, ctx.llm_settings


class IdeaAdapter:
    node_id = "IDEA"
    artifact_type = IDEA_CANDIDATE_ARTIFACT_TYPE
    model_class = IdeaCandidate

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        return True  # root of the chain -- nothing upstream to compare against

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        if ctx.initial_idea_input is None:
            raise ProductionInputMissingError(
                "ExecutionContext.initial_idea_input (an IdeaEngineInput -- seed/domain/"
                "discovery_mode/additional_context) must be supplied before IDEA can execute; "
                "the runner never hallucinates the initial brief."
            )
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine_input = ctx.initial_idea_input.model_copy(update={"project_id": ctx.project_id})
        engine = IdeaEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(engine_input)
        return NodeExecutionResult(artifact_id=result.idea.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class ResearchR0Adapter:
    node_id = "RESEARCH_R0"
    artifact_type = RESEARCH_R0_ARTIFACT_TYPE
    model_class = ResearchR0

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        idea = _load_optional(ctx.db_engine, ctx.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
        return idea is not None and artifact.idea_id == idea.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, _global_config, llm_settings = _require_llm_context(ctx)
        if ctx.research_retriever is None:
            raise AdapterConfigurationError("ExecutionContext.research_retriever must be set for RESEARCH_R0.")
        engine = R0ResearchEngine(ctx.db_engine, provider, ctx.research_retriever, llm_settings)
        result = engine.run(R0ResearchInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.research.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class FeasibilityAdapter:
    node_id = "FEASIBILITY"
    artifact_type = FEASIBILITY_REPORT_ARTIFACT_TYPE
    model_class = FeasibilityReport
    gate_not_ready_reason = BlockedReason.FEASIBILITY_NOT_PASSED

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        idea = _load_optional(ctx.db_engine, ctx.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
        research_r0 = _load_optional(ctx.db_engine, ctx.project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)
        return _module_run_input_ids_match(
            ctx.db_engine, ctx.project_id, "feasibility_engine",
            [idea.id if idea else None, research_r0.id if research_r0 else None],
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = FeasibilityEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(FeasibilityEngineInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.feasibility.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return artifact.status is GateStatus.PASS


class ResearchR1Adapter:
    node_id = "RESEARCH_R1"
    artifact_type = RESEARCH_R1_ARTIFACT_TYPE
    model_class = ResearchPackage

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        idea = _load_optional(ctx.db_engine, ctx.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
        research_r0 = _load_optional(ctx.db_engine, ctx.project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)
        feasibility = _load_optional(ctx.db_engine, ctx.project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
        return _module_run_input_ids_match(
            ctx.db_engine, ctx.project_id, "research_r1_engine",
            [
                idea.id if idea else None,
                research_r0.id if research_r0 else None,
                feasibility.id if feasibility else None,
            ],
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        if ctx.research_retriever is None:
            raise AdapterConfigurationError("ExecutionContext.research_retriever must be set for RESEARCH_R1.")
        engine = R1ResearchEngine(ctx.db_engine, provider, ctx.research_retriever, global_config, llm_settings)
        result = engine.run(R1ResearchInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.research.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class NarrativeAdapter:
    node_id = "NARRATIVE"
    artifact_type = NARRATIVE_PLAN_ARTIFACT_TYPE
    model_class = NarrativePlan

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        idea = _load_optional(ctx.db_engine, ctx.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
        research_package = _load_optional(ctx.db_engine, ctx.project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
        return _module_run_input_ids_match(
            ctx.db_engine, ctx.project_id, "narrative_engine",
            [idea.id if idea else None, research_package.id if research_package else None],
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = NarrativeEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(NarrativeEngineInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.narrative.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class PackagingP0Adapter:
    node_id = "PACKAGING_P0"
    artifact_type = PACKAGING_PROTOTYPE_ARTIFACT_TYPE
    model_class = PackagingPrototype
    gate_not_ready_reason = BlockedReason.PACKAGING_RISK_TOO_HIGH

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        idea = _load_optional(ctx.db_engine, ctx.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
        research_package = _load_optional(ctx.db_engine, ctx.project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
        narrative = _load_optional(ctx.db_engine, ctx.project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
        return _module_run_input_ids_match(
            ctx.db_engine, ctx.project_id, "packaging_p0_engine",
            [
                idea.id if idea else None,
                research_package.id if research_package else None,
                narrative.id if narrative else None,
            ],
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = PackagingP0Engine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(PackagingP0Input(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.packaging.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return artifact.risk_of_misleading is not RiskLevel.HIGH


class ScriptAdapter:
    node_id = "SCRIPT"
    artifact_type = SCRIPT_PLAN_ARTIFACT_TYPE
    model_class = ScriptPlan

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        idea = _load_optional(ctx.db_engine, ctx.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
        research_package = _load_optional(ctx.db_engine, ctx.project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
        narrative = _load_optional(ctx.db_engine, ctx.project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
        packaging = _load_optional(ctx.db_engine, ctx.project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
        return _module_run_input_ids_match(
            ctx.db_engine, ctx.project_id, "script_engine",
            [
                idea.id if idea else None,
                research_package.id if research_package else None,
                narrative.id if narrative else None,
                packaging.id if packaging else None,
            ],
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = ScriptEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(ScriptEngineInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.script.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class ScriptVerifyAdapter:
    node_id = "SCRIPT_VERIFY"
    artifact_type = SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
    model_class = ScriptVerificationReport
    gate_not_ready_reason = BlockedReason.SCRIPT_VERIFICATION_NOT_PASSED

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        research_package = _load_optional(ctx.db_engine, ctx.project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
        narrative = _load_optional(ctx.db_engine, ctx.project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
        packaging = _load_optional(ctx.db_engine, ctx.project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
        script_plan = _load_optional(ctx.db_engine, ctx.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
        return _module_run_input_ids_match(
            ctx.db_engine, ctx.project_id, "script_verification_engine",
            [
                research_package.id if research_package else None,
                narrative.id if narrative else None,
                packaging.id if packaging else None,
                script_plan.id if script_plan else None,
            ],
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = ScriptVerificationEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(ScriptVerificationInput(project_id=ctx.project_id))
        # ScriptVerificationReport has no `id` field (a locked Phase 1
        # contract) -- module_run_id is the only durable reference.
        return NodeExecutionResult(artifact_id=result.module_run_id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return artifact.status is GateStatus.PASS


class VoicePlanAdapter:
    node_id = "VOICE_PLAN"
    artifact_type = VOICE_PLAN_ARTIFACT_TYPE
    model_class = VoicePlan

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        script_plan = _load_optional(ctx.db_engine, ctx.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
        return script_plan is not None and artifact.script_plan_id == script_plan.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = VoicePlanningEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(VoicePlanningInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.voice_plan.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class VisualPlanAdapter:
    node_id = "VISUAL_PLAN"
    artifact_type = VISUAL_PLAN_ARTIFACT_TYPE
    model_class = VisualPlan

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        script_plan = _load_optional(ctx.db_engine, ctx.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
        voice_plan = _load_optional(ctx.db_engine, ctx.project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
        if script_plan is None or voice_plan is None:
            return False
        return artifact.script_plan_id == script_plan.id and artifact.voice_plan_id == voice_plan.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = VisualPlanningEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(VisualPlanningInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.visual_plan.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class AssemblyPlanAdapter:
    node_id = "ASSEMBLY_PLAN"
    artifact_type = ASSEMBLY_PLAN_ARTIFACT_TYPE
    model_class = AssemblyPlan

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        script_plan = _load_optional(ctx.db_engine, ctx.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
        voice_plan = _load_optional(ctx.db_engine, ctx.project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
        visual_plan = _load_optional(ctx.db_engine, ctx.project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
        if script_plan is None or voice_plan is None or visual_plan is None:
            return False
        return (
            artifact.script_plan_id == script_plan.id
            and artifact.voice_plan_id == voice_plan.id
            and artifact.visual_plan_id == visual_plan.id
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = AssemblyPlanningEngine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(AssemblyPlanningInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.assembly_plan.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class PackagingP1Adapter:
    node_id = "PACKAGING_P1"
    artifact_type = PACKAGING_P1_ARTIFACT_TYPE
    model_class = FinalPackagingPlan

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        packaging = _load_optional(ctx.db_engine, ctx.project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
        script_plan = _load_optional(ctx.db_engine, ctx.project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
        visual_plan = _load_optional(ctx.db_engine, ctx.project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
        assembly_plan = _load_optional(ctx.db_engine, ctx.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
        if packaging is None or script_plan is None or visual_plan is None or assembly_plan is None:
            return False
        return (
            artifact.packaging_prototype_id == packaging.id
            and artifact.script_plan_id == script_plan.id
            and artifact.visual_plan_id == visual_plan.id
            and artifact.assembly_plan_id == assembly_plan.id
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        provider, global_config, llm_settings = _require_llm_context(ctx)
        engine = PackagingP1Engine(ctx.db_engine, provider, global_config, llm_settings)
        result = engine.run(PackagingP1Input(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.final_packaging_plan.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True
