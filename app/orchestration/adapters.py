"""Real Phase 33 adapters for the wired production slice, plus the
default 19-node graph combining them with UnwiredNodeAdapter for
everything this phase does not execute.

Wired (7 nodes) -- confirmed app.llm-free renderers/builders with
consistent constructor and `.run()` conventions:
VOICE_RENDER, VISUAL_RENDER, TIMELINE, VIDEO_RENDER, CAPTION_BUILD,
SUBTITLE_EXPORT, MEDIA_QC.

Unwired (12 nodes) -- every earlier creative-planning stage's own
engine.py imports app.llm directly (confirmed by inspection before this
phase was written): IDEA, FEASIBILITY, RESEARCH_R0, RESEARCH_R1,
NARRATIVE, PACKAGING_P0, SCRIPT, SCRIPT_VERIFY, VOICE_PLAN, VISUAL_PLAN,
ASSEMBLY_PLAN, PACKAGING_P1. Each is registered with UnwiredNodeAdapter
(app/orchestration/registry.py) -- present in the graph's own topology
for accurate, complete documentation of the real pipeline's dependency
order, but never executable by ProductionRunner. See that class's own
docstring for the full rationale.

SUBTITLE_EXPORT models Phase 31's own SubtitleRenderer.run() as ONE
node covering BOTH SRT export (always) and caption burn-in (opt-in via
ExecutionContext.burn_in_settings) -- the real underlying API executes
them together in a single call, so a separate SUBTITLE_BURN_IN graph
node was deliberately not added (it would either duplicate the SRT
export work or require awkward cross-node state sharing for no real
benefit). MediaQCAdapter independently discovers whether a
CaptionedVideoAsset exists (exactly as the real MediaQCRenderer already
does), so this collapse costs nothing downstream.

Dependencies below are the REAL technical prerequisites each renderer's
own freshness chain actually checks (confirmed by inspection), not
merely the narrative pipeline order: PACKAGING_P1, for example, is
sequenced after ASSEMBLY_PLAN in the project's own pipeline narrative,
but no renderer's own freshness chain ever consults it, so it is not a
dependency of VOICE_RENDER/VISUAL_RENDER/TIMELINE here (requirement #4:
graph definitions are code-defined, never inferred, and are set to
match reality, not a marketing diagram).
"""

from __future__ import annotations

from app.models.assembly import AssemblyPlan
from app.models.audio import VoiceRenderManifest
from app.models.caption import CaptionManifest
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.research import ResearchPackage, ResearchR0
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.models.subtitle import SubtitleFileAsset
from app.models.timeline import TimelineManifest
from app.models.video import EncodedVideoAsset
from app.models.visual import VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoicePlan
from app.media_qc.models import MediaQCReport
from app.orchestration.graph import ProductionGraph, ProductionNodeDefinition
from app.orchestration.models import ApprovalGateType, BlockedReason
from app.orchestration.registry import ExecutionContext, NodeExecutionResult, UnwiredNodeAdapter
from app.renderers.caption.builder import CaptionBuilder
from app.renderers.caption.models import CAPTION_MANIFEST_ARTIFACT_TYPE, CaptionBuilderInput
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCRendererInput
from app.renderers.media_qc.renderer import MediaQCRenderer
from app.renderers.subtitle.models import SUBTITLE_FILE_ASSET_ARTIFACT_TYPE, SubtitleRendererInput
from app.renderers.subtitle.renderer import SubtitleRenderer
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineBuilderInput
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, VideoRendererInput
from app.renderers.video.renderer import VideoRenderer
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRendererInput
from app.renderers.voice.renderer import VoiceRenderer
from app.storage.artifacts import get_artifact
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"
_ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"


def _load_optional(engine, project_id, artifact_type, model_class):
    try:
        return get_artifact(engine, project_id, artifact_type, model_class)
    except (ArtifactNotFoundError, ArtifactValidationError):
        return None


class VoiceRenderAdapter:
    node_id = "VOICE_RENDER"
    artifact_type = VOICE_RENDER_MANIFEST_ARTIFACT_TYPE
    model_class = VoiceRenderManifest

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        voice_plan = _load_optional(ctx.db_engine, ctx.project_id, _VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
        return voice_plan is not None and artifact.voice_plan_id == voice_plan.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        renderer = VoiceRenderer(ctx.db_engine, ctx.tts_provider, ctx.tts_settings, ctx.audio_store)
        result = renderer.run(VoiceRendererInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.manifest.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class VisualRenderAdapter:
    node_id = "VISUAL_RENDER"
    artifact_type = VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
    model_class = VisualRenderManifest

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        voice_plan = _load_optional(ctx.db_engine, ctx.project_id, _VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
        visual_plan = _load_optional(ctx.db_engine, ctx.project_id, _VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
        if voice_plan is None or visual_plan is None:
            return False
        return artifact.voice_plan_id == voice_plan.id and artifact.visual_plan_id == visual_plan.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        renderer = VisualRenderer(
            ctx.db_engine, ctx.visual_provider, ctx.visual_settings, ctx.visual_store,
            ti_compositor=ctx.ti_compositor, diagram_renderer=ctx.diagram_renderer,
            layer_compositor=ctx.layer_compositor,
        )
        result = renderer.run(
            VisualRendererInput(
                project_id=ctx.project_id, ti_state_sources=ctx.ti_state_sources,
                composition_specs=ctx.composition_specs,
            )
        )
        return NodeExecutionResult(artifact_id=result.manifest.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class TimelineAdapter:
    node_id = "TIMELINE"
    artifact_type = TIMELINE_MANIFEST_ARTIFACT_TYPE
    model_class = TimelineManifest

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        assembly_plan = _load_optional(ctx.db_engine, ctx.project_id, _ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
        voice_render_manifest = _load_optional(
            ctx.db_engine, ctx.project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest
        )
        visual_render_manifest = _load_optional(
            ctx.db_engine, ctx.project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest
        )
        if assembly_plan is None or voice_render_manifest is None or visual_render_manifest is None:
            return False
        return (
            artifact.assembly_plan_id == assembly_plan.id
            and artifact.voice_render_manifest_id == voice_render_manifest.id
            and artifact.visual_render_manifest_id == visual_render_manifest.id
        )

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        builder = TimelineBuilder(ctx.db_engine, ctx.audio_store, ctx.visual_store)
        result = builder.run(TimelineBuilderInput(project_id=ctx.project_id, visual_motions=ctx.visual_motions))
        return NodeExecutionResult(artifact_id=result.manifest.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class VideoRenderAdapter:
    node_id = "VIDEO_RENDER"
    artifact_type = ENCODED_VIDEO_ASSET_ARTIFACT_TYPE
    model_class = EncodedVideoAsset

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        timeline_manifest = _load_optional(
            ctx.db_engine, ctx.project_id, TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineManifest
        )
        return timeline_manifest is not None and artifact.timeline_manifest_id == timeline_manifest.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        renderer = VideoRenderer(ctx.db_engine, ctx.audio_store, ctx.visual_store, ctx.video_store)
        result = renderer.run(VideoRendererInput(project_id=ctx.project_id, audio_bindings=ctx.audio_bindings))
        return NodeExecutionResult(artifact_id=result.asset.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class CaptionBuildAdapter:
    node_id = "CAPTION_BUILD"
    artifact_type = CAPTION_MANIFEST_ARTIFACT_TYPE
    model_class = CaptionManifest

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        timeline_manifest = _load_optional(
            ctx.db_engine, ctx.project_id, TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineManifest
        )
        return timeline_manifest is not None and artifact.timeline_manifest_id == timeline_manifest.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        builder = CaptionBuilder(ctx.db_engine)
        result = builder.run(CaptionBuilderInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.manifest.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class SubtitleExportAdapter:
    node_id = "SUBTITLE_EXPORT"
    artifact_type = SUBTITLE_FILE_ASSET_ARTIFACT_TYPE
    model_class = SubtitleFileAsset

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        caption_manifest = _load_optional(
            ctx.db_engine, ctx.project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, CaptionManifest
        )
        return caption_manifest is not None and artifact.caption_manifest_id == caption_manifest.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        renderer = SubtitleRenderer(ctx.db_engine, ctx.subtitle_store, ctx.video_store)
        result = renderer.run(
            SubtitleRendererInput(project_id=ctx.project_id, burn_in_settings=ctx.burn_in_settings)
        )
        return NodeExecutionResult(artifact_id=result.subtitle_asset.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        return True


class MediaQCAdapter:
    node_id = "MEDIA_QC"
    artifact_type = MEDIA_QC_REPORT_ARTIFACT_TYPE
    model_class = MediaQCReport
    gate_not_ready_reason = BlockedReason.QC_NOT_READY

    def load_current(self, ctx: ExecutionContext):
        return _load_optional(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        timeline_manifest = _load_optional(
            ctx.db_engine, ctx.project_id, TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineManifest
        )
        return timeline_manifest is not None and artifact.timeline_manifest_id == timeline_manifest.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        renderer = MediaQCRenderer(ctx.db_engine, ctx.video_store, ctx.subtitle_store)
        result = renderer.run(MediaQCRendererInput(project_id=ctx.project_id))
        return NodeExecutionResult(artifact_id=result.report.id, module_run_id=result.module_run_id)

    def gate_ok(self, artifact) -> bool:
        """Requirement #16: QC FAIL blocks FINAL_MEDIA_APPROVAL outright
        (the gate is never even offered); PASS/WARN both permit
        proceeding to wait for the human decision. Never auto-approves."""
        return artifact.ready_for_human_review


# ---------------------------------------------------------------------------
# Default 19-node graph
# ---------------------------------------------------------------------------

_UNWIRED_ARTIFACTS: dict[str, tuple[str, type]] = {
    "IDEA": ("idea_candidate", IdeaCandidate),
    "RESEARCH_R0": ("research_r0", ResearchR0),
    "FEASIBILITY": ("feasibility_report", FeasibilityReport),
    "RESEARCH_R1": ("research_r1", ResearchPackage),
    "NARRATIVE": ("narrative_plan", NarrativePlan),
    "PACKAGING_P0": ("packaging_prototype", PackagingPrototype),
    "SCRIPT": (_SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan),
    "SCRIPT_VERIFY": ("script_verification_report", ScriptVerificationReport),
    "VOICE_PLAN": (_VOICE_PLAN_ARTIFACT_TYPE, VoicePlan),
    "VISUAL_PLAN": (_VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan),
    "ASSEMBLY_PLAN": (_ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan),
    "PACKAGING_P1": ("packaging_p1", FinalPackagingPlan),
}

_NODE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "IDEA": (),
    "RESEARCH_R0": ("IDEA",),
    "FEASIBILITY": ("RESEARCH_R0",),
    "RESEARCH_R1": ("FEASIBILITY",),
    "NARRATIVE": ("RESEARCH_R1",),
    "PACKAGING_P0": ("NARRATIVE",),
    "SCRIPT": ("PACKAGING_P0",),
    "SCRIPT_VERIFY": ("SCRIPT",),
    "VOICE_PLAN": ("SCRIPT_VERIFY",),
    "VISUAL_PLAN": ("VOICE_PLAN",),
    "ASSEMBLY_PLAN": ("VOICE_PLAN", "VISUAL_PLAN"),
    "PACKAGING_P1": ("ASSEMBLY_PLAN",),
    "VOICE_RENDER": ("VOICE_PLAN",),
    "VISUAL_RENDER": ("VISUAL_PLAN",),
    "TIMELINE": ("ASSEMBLY_PLAN", "VOICE_RENDER", "VISUAL_RENDER"),
    "VIDEO_RENDER": ("TIMELINE",),
    "CAPTION_BUILD": ("TIMELINE",),
    "SUBTITLE_EXPORT": ("CAPTION_BUILD", "VIDEO_RENDER"),
    "MEDIA_QC": ("VIDEO_RENDER", "SUBTITLE_EXPORT"),
}

_GATE_AFTER: dict[str, ApprovalGateType] = {
    "MEDIA_QC": ApprovalGateType.FINAL_MEDIA_APPROVAL,
}


def build_default_graph() -> ProductionGraph:
    definitions = [
        ProductionNodeDefinition(
            node_id=node_id, dependencies=dependencies, gate_after=_GATE_AFTER.get(node_id)
        )
        for node_id, dependencies in _NODE_DEPENDENCIES.items()
    ]
    return ProductionGraph(definitions)


def build_default_adapters() -> dict[str, object]:
    adapters: dict[str, object] = {
        "VOICE_RENDER": VoiceRenderAdapter(),
        "VISUAL_RENDER": VisualRenderAdapter(),
        "TIMELINE": TimelineAdapter(),
        "VIDEO_RENDER": VideoRenderAdapter(),
        "CAPTION_BUILD": CaptionBuildAdapter(),
        "SUBTITLE_EXPORT": SubtitleExportAdapter(),
        "MEDIA_QC": MediaQCAdapter(),
    }
    for node_id, (artifact_type, model_class) in _UNWIRED_ARTIFACTS.items():
        adapters[node_id] = UnwiredNodeAdapter(node_id, artifact_type, model_class)
    return adapters


WIRED_NODE_IDS = frozenset(
    {"VOICE_RENDER", "VISUAL_RENDER", "TIMELINE", "VIDEO_RENDER", "CAPTION_BUILD", "SUBTITLE_EXPORT", "MEDIA_QC"}
)
UNWIRED_NODE_IDS = frozenset(_UNWIRED_ARTIFACTS)
