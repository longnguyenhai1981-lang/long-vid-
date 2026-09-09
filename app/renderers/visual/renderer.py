"""VisualRenderer: turns a locked VisualPlan into rendered visual asset
files and unrendered-requirement bookkeeping -- the visual counterpart to
app/renderers/voice/renderer.py.

MVP_COMPLETE -> load+verify ScriptPlan -> load+verify VoicePlan -> verify
VoicePlan freshness against the current ScriptPlan -> load+verify
VisualPlan -> verify VisualPlan freshness against the current ScriptPlan
and VoicePlan -> for every VisualBeat, in order, route it deterministically
by media_type: GENERATED_STILL calls a VisualProvider (retrying only a
provider-side failure, up to a bounded number of attempts) and writes the
returned bytes to disk via VisualFileStore (no retry on a write failure);
DIAGRAM (Phase 25, see below) never calls a provider and instead renders
deterministically and locally via DiagramRenderer; COMPOSITION (Phase 26,
see below) never calls a provider and instead composites other beats'
already-rendered outputs via VisualLayerCompositor; ASSET_REUSE never
calls a provider and instead becomes a VisualRenderRequirement record;
TI_STATE (Phase 23, see below) resolves a canonical Tí asset
deterministically instead of ever calling a provider; LIMITED_MOTION/
EVIDENCE_MEDIA/AI_HERO_VIDEO never call a provider and instead become a
VisualRenderRequirement record -> build a VisualRenderManifest -> validate
its own integrity as defense in depth -> persist the manifest artifact ->
persist ModuleRun.

Phase 26 -- deterministic visual layer composition. COMPOSITION sits ABOVE
GENERATED_STILL/TI_STATE/DIAGRAM without replacing or modifying any of
them: it combines their already-rendered raster outputs into one final
frame via app/layer_compositor/'s VisualLayerCompositor, itself a
standalone, dependency-free deterministic service (no injected
dependency, exactly like DiagramRenderer). `_validate_composition_specs`
checks up front that every COMPOSITION beat has a
VisualRendererInput.composition_specs entry (CompositionSpec -- there is
no default, unlike TI_STATE's STANDALONE fallback), raising
MissingCompositionSpecError immediately if not.
`_build_background_dependency_edges` is generalized (via
`_iter_beat_to_beat_references`) to also turn every
CompositionSpec.layers[*].source_beat_id into a dependency edge, reusing
the exact same Phase 24 topological-ordering machinery TI_STATE's
background_beat_id already used -- no second dependency engine.
`_render_composition_beat` then resolves each layer (`_resolve_
composition_layer`/`_resolve_composition_layer_path`) from THIS SAME
render pass's own results -- a RenderedVisualAsset (GENERATED_STILL/
DIAGRAM/TI_STATE COMPOSITE/another COMPOSITION) resolved via
VisualFileStore, OR (deliberately broader than TI_STATE's own background
rule) a standalone TI_STATE CANONICAL_ASSET_READY requirement resolved
via the injected TiCompositor's retriever -- and hands the resolved
layers to VisualLayerCompositor.compose(), producing a real PNG
RenderedVisualAsset. Zero provider calls, zero retries.

Phase 25 -- DIAGRAM no longer calls a VisualProvider at all. Previously
routed exactly like GENERATED_STILL (a generic AI-generated still image);
now `_validate_diagram_specs` checks up front that every DIAGRAM beat
carries a `diagram_spec` (app/models/diagram.py -- a fully self-contained,
declarative, normalized-coordinate diagram: canvas + explicit element
list) and every non-DIAGRAM beat does not, raising MissingDiagramSpecError/
UnexpectedDiagramSpecError immediately if not. `_render_diagram_beat` then
hands that spec directly to `DiagramRenderer.render()`
(app/diagram_renderer/renderer.py, Pillow-based, no injected dependency,
no configuration) to produce a real PNG `RenderedVisualAsset` -- zero
provider calls, zero retries (a deterministic local operation, like
TiCompositor). Every concrete VisualProvider remains a scene/background
image generator only; this renderer's own DIAGRAM handling is the only
thing that changed.

Phase 23 -- TI_STATE routes through an injected TiCompositor instead of
producing the old "ti_state:<VisualTiState value>" placeholder reference:

1. beat.ti_state (a VisualTiState) is resolved to a canonical TiState via
   the existing deterministic to_ti_state() mapping (Phase 21.1) -- no
   fuzzy inference, ever.
2. The caller decides the mode explicitly per beat, via
   VisualRendererInput.ti_state_sources[beat_id] (a TiStateSource; a beat
   with no entry defaults to STANDALONE -- never inferred from beat
   content):
   - STANDALONE (default): the canonical TiAsset is resolved via
     TiCompositor.retriever.get_asset() and recorded as a
     VisualRenderRequirement with status CANONICAL_ASSET_READY -- no
     compositing, no file written, exactly like the old REUSE_ONLY path's
     "no provider call, no bytes" shape, just resolved for real instead of
     left as a placeholder string.
   - COMPOSITE: only reachable when the caller supplies an explicit
     background_path -- TiCompositor.composite() renders Tí onto that
     background and the result becomes a RenderedVisualAsset (a real PNG
     file, same as a GENERATED_STILL/DIAGRAM asset).
3. TiCompositor is optional (ti_compositor=None is a valid VisualRenderer
   configuration) -- but any TI_STATE beat encountered without one raises
   TiCompositorNotConfiguredError immediately. No provider is ever called
   for a TI_STATE beat, in either mode -- there is no AI-generated-Tí
   fallback, by construction: this renderer only ever sends
   GENERATED_STILL beats to a VisualProvider (see
   _PROVIDER_RENDERABLE_MEDIA_TYPES below; DIAGRAM was removed from this
   set in Phase 25).

IMPORTANT: this package has ZERO app.llm dependency, direct or transitive
-- there is no prompt here and nothing here reasons about content. The
three upstream artifact-type constants below are deliberately duplicated
rather than imported from app.engines.script.models /
app.engines.voice_plan.models / app.engines.visual_plan.models, because
importing any of them would transitively pull in app.llm.models (see the
comment at their definition, and app/renderers/voice/renderer.py's
identical precedent). app/ti_compositor/ and app/ti_assets/ (Phase 23's
new dependencies here) are equally free of app.llm, confirmed by
tests/test_visual_renderer.py's existing subprocess-based import audit,
unmodified. Like every engine, this renderer is artifact-driven, not
workflow-driven: it never transitions project state (MVP_COMPLETE is
unchanged before and after it runs) and never updates any Project
reference field (no visual_render_manifest_id field exists on Project --
confirmed absent, not added this phase). It also never re-saves or
modifies the ScriptPlan, VoicePlan, or VisualPlan it renders. No real
image/video vendor integration beyond GENERATED_STILL's existing
VisualProvider call, no SVG/charting/LaTeX engine (DiagramRenderer's own
scope is deliberately limited -- see app/diagram_renderer/renderer.py), no
web footage retrieval, no CapCut automation, no visual post-processing, no
thumbnail rendering, no publishing, no orchestrator.

Phase 24 -- beat-to-beat visual composition. A TI_STATE COMPOSITE beat's
background may now be sourced from ANOTHER VisualBeat's own rendered
output instead of only an explicit background_path: TiStateSource gains a
mutually-exclusive background_beat_id (app/renderers/visual/models.py).
This module owns the two pieces that make that safe and deterministic:

1. _build_background_dependency_edges walks every TI_STATE beat's
   TiStateSource once, up front, and turns each background_beat_id into a
   dependency edge (background beat -> dependent beat), failing fast --
   before any rendering, any provider call, any ModuleRun write -- on an
   unknown beat_id (UnknownBackgroundBeatError) or a self-reference
   (SelfReferentialBackgroundBeatError). No fuzzy matching, no "nearest
   previous beat" -- an edge exists only where an explicit
   background_beat_id names an explicit, exactly-matching beat_id.
2. _topological_beat_order turns those edges into a single rendering
   order for _render_all's loop: a dependency always precedes its
   dependent, a cycle fails loudly (BackgroundBeatCycleError) before any
   beat renders, and -- critically -- the authored VisualPlan.beats order
   is preserved wherever the dependency graph does not force a beat
   later. This is a plain deterministic topological sort (Kahn's
   algorithm, always breaking ties toward the earliest authored index),
   not a general workflow/orchestrator framework.

Once a background beat renders (GENERATED_STILL/DIAGRAM via a
VisualProvider, or TI_STATE COMPOSITE via TiCompositor -- STANDALONE
TI_STATE, ASSET_REUSE, and every EXTERNAL_REQUIRED media type never
produce a RenderedVisualAsset and so can never satisfy a
background_beat_id), its RenderedVisualAsset is looked up from THIS SAME
render pass's in-memory results (never re-rendered, never regenerated)
and resolved to an absolute path via VisualFileStore.root / file_path;
that path is handed to TiCompositor exactly as an explicit
background_path always was. Nothing is copied, mutated, or duplicated,
and zero extra provider calls happen for the dependent beat -- see
_resolve_composite_background_path below. A background beat that
resolved to a requirement instead of an asset, or whose asset file is
missing on disk, fails explicitly (BackgroundBeatNotRenderedError /
BackgroundBeatAssetMissingError) rather than silently falling back to
STANDALONE or calling a VisualProvider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from app.diagram_renderer.errors import DiagramRenderError
from app.diagram_renderer.renderer import DiagramRenderer
from app.layer_compositor.compositor import VisualLayerCompositor
from app.layer_compositor.errors import LayerCompositionError
from app.layer_compositor.models import BackgroundLayer, LayerCompositionSpec, LayerSourceType, OverlayLayer
from app.models.common import ModuleRunStatus, ProjectState, VisualMediaType, VisualRequirementStatus
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.ti_assets import TiState
from app.models.visual import VisualBeat, VisualPlan
from app.models.visual_render import RenderedVisualAsset, VisualRenderManifest, VisualRenderRequirement
from app.models.voice import VoicePlan
from app.renderers.visual.errors import (
    BackgroundBeatAssetMissingError,
    BackgroundBeatCycleError,
    BackgroundBeatNotRenderedError,
    CompositionSourceNotRenderedError,
    InvalidVisualBeatError,
    MissingCompositionSpecError,
    MissingDiagramSpecError,
    MissingScriptPlanArtifactError,
    MissingVisualPlanArtifactError,
    MissingVoicePlanArtifactError,
    RendererStateError,
    SelfReferentialBackgroundBeatError,
    StaleVisualPlanError,
    StaleVoicePlanError,
    TiCompositorNotConfiguredError,
    UnexpectedDiagramSpecError,
    UnknownBackgroundBeatError,
    VisualRenderManifestIntegrityError,
)
from app.renderers.visual.models import (
    VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE,
    CompositionLayerSource,
    CompositionSpec,
    TiStateRenderMode,
    TiStateSource,
    VisualRendererInput,
    VisualRendererResult,
)
from app.renderers.visual.validation import validate_visual_render_manifest
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.ti_assets.visual_state_mapping import to_ti_state
from app.ti_compositor.compositor import TiCompositor
from app.ti_compositor.models import TiAnchor, TiCompositeRequest, TiPlacement, TiScalePolicy
from app.visual.config import VisualSettings
from app.visual.errors import VisualProviderError
from app.visual.models import VisualRenderRequest, VisualRenderResponse
from app.visual.provider import VisualProvider
from app.visual.storage import VisualFileStore

# Applied whenever a TI_STATE beat composites and its TiStateSource does not
# specify its own placement -- a modest bottom-right cameo that rarely
# collides with a scene's primary subject. Not configurable per-project in
# Phase 23; a caller needing a different default supplies an explicit
# TiPlacement via TiStateSource.placement instead.
_DEFAULT_TI_PLACEMENT = TiPlacement(
    anchor=TiAnchor.BOTTOM_RIGHT,
    scale=TiScalePolicy(relative_height=0.35),
)

# Duplicated from app.engines.script.models.SCRIPT_PLAN_ARTIFACT_TYPE,
# app.engines.voice_plan.models.VOICE_PLAN_ARTIFACT_TYPE, and
# app.engines.visual_plan.models.VISUAL_PLAN_ARTIFACT_TYPE rather than
# imported: each of those modules imports app.llm.models (for its own
# TokenUsage-carrying *Result types), and this package must stay free of
# app.llm even transitively. These string values are load-bearing keys into
# generic artifact storage, not engine reasoning logic.
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"

MODULE_NAME = "visual_renderer"
MODULE_VERSION = "0.1"

# Deterministic media router (Phase 19; DIAGRAM split out in Phase 25) --
# no LLM decision here. GENERATED_STILL is the only media type this
# renderer still ever sends to a VisualProvider. DIAGRAM used to be routed
# alongside it (through the same VisualProvider call) but Phase 25 gives it
# its own dedicated, provider-free routing branch in _render_all, calling
# DiagramRenderer directly instead (see _render_diagram_beat below) -- it
# stays out of this set specifically so it can never again reach
# _render_one_beat/VisualProvider.render(). TI_STATE is likewise absent
# from every set below -- it has its own dedicated routing branch in
# _render_all (Phase 23), since unlike ASSET_REUSE it may produce either a
# RenderedVisualAsset (COMPOSITE mode) or a VisualRenderRequirement
# (STANDALONE mode), decided per beat, not by media_type alone.
_PROVIDER_RENDERABLE_MEDIA_TYPES = {VisualMediaType.GENERATED_STILL}
_REUSE_ONLY_MEDIA_TYPES = {VisualMediaType.ASSET_REUSE}
_EXTERNAL_REQUIRED_MEDIA_TYPES = {
    VisualMediaType.LIMITED_MOTION,
    VisualMediaType.EVIDENCE_MEDIA,
    VisualMediaType.AI_HERO_VIDEO,
}


class VisualRenderer:
    def __init__(
        self,
        db_engine: Engine,
        visual_provider: VisualProvider,
        visual_settings: VisualSettings,
        visual_store: VisualFileStore,
        *,
        ti_compositor: TiCompositor | None = None,
        diagram_renderer: DiagramRenderer | None = None,
        layer_compositor: VisualLayerCompositor | None = None,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._visual_provider = visual_provider
        self._visual_settings = visual_settings
        self._visual_store = visual_store
        self._ti_compositor = ti_compositor
        # Unlike ti_compositor, DiagramRenderer/VisualLayerCompositor have no
        # external state (no database, no file-backed asset set) and no
        # "unconfigured" failure mode -- both are always available, so this
        # renderer instantiates a default one of each itself rather than
        # requiring every caller to supply one. Each parameter exists only
        # so a test can inject a substitute.
        self._diagram_renderer = diagram_renderer if diagram_renderer is not None else DiagramRenderer()
        self._layer_compositor = layer_compositor if layer_compositor is not None else VisualLayerCompositor()
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, renderer_input: VisualRendererInput) -> VisualRendererResult:
        project = self._project_repo.get_project(self._db_engine, renderer_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"VisualRenderer requires project state MVP_COMPLETE, got {project.state.value}"
            )
        script_plan = self._load_and_verify_script_plan(project)
        voice_plan = self._load_and_verify_voice_plan(project)
        self._verify_voice_plan_is_fresh(project, voice_plan)
        visual_plan = self._load_and_verify_visual_plan(project)
        self._verify_visual_plan_is_fresh(project, voice_plan, visual_plan)

        run_record = ModuleRun(
            project_id=renderer_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(script_plan.id), str(voice_plan.id), str(visual_plan.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            manifest, provider_call_count = self._render_all(
                renderer_input, script_plan, voice_plan, visual_plan
            )

            issues = validate_visual_render_manifest(manifest, script_plan, voice_plan, visual_plan)
            if issues:
                raise VisualRenderManifestIntegrityError(issues)

            self._artifact_repo.save_artifact(
                self._db_engine,
                renderer_input.project_id,
                VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE,
                manifest,
            )
            # No Project reference update: no visual_render_manifest_id field
            # exists on Project (confirmed absent, Phase 19 compat finding).
            # No state transition: the project stays in MVP_COMPLETE -- this
            # renderer is artifact-driven, not workflow-driven.
        except Exception as exc:
            # Broad on purpose: any failure from rendering through
            # persistence must be recorded as a FAILED audit record and
            # re-raised unchanged -- mirrors every prior renderer/engine.
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, manifest.id)
        )

        return VisualRendererResult(
            manifest=manifest,
            module_run_id=run_record.run_id,
            provider_call_count=provider_call_count,
            rendered_asset_count=len(manifest.assets),
            external_requirement_count=sum(
                1
                for req in manifest.requirements
                if req.status is VisualRequirementStatus.EXTERNAL_REQUIRED
            ),
            reuse_requirement_count=sum(
                1
                for req in manifest.requirements
                if req.status is VisualRequirementStatus.REUSE_ONLY
            ),
            canonical_asset_ready_count=sum(
                1
                for req in manifest.requirements
                if req.status is VisualRequirementStatus.CANONICAL_ASSET_READY
            ),
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
        """No provider call, no ModuleRun, no file write happens before this
        check. VoicePlan must have been generated for the ScriptPlan the
        project currently references."""
        if voice_plan.script_plan_id != project.script_plan_id:
            raise StaleVoicePlanError(
                f"The current VoicePlan for project {project.project_id} was "
                f"generated for ScriptPlan {voice_plan.script_plan_id}, but the "
                f"project currently references ScriptPlan "
                f"{project.script_plan_id}; rerun Voice Planning first"
            )

    def _load_and_verify_visual_plan(self, project: Project) -> VisualPlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVisualPlanArtifactError(
                f"Project {project.project_id} has no valid VisualPlan artifact"
            ) from exc

    def _verify_visual_plan_is_fresh(
        self, project: Project, voice_plan: VoicePlan, visual_plan: VisualPlan
    ) -> None:
        """A real production-integrity gate: no provider call, no ModuleRun,
        no file write happens before this check. VisualPlan must have been
        generated for the ScriptPlan the project currently references, and
        for the VoicePlan currently in force."""
        if visual_plan.script_plan_id != project.script_plan_id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was "
                f"generated for ScriptPlan {visual_plan.script_plan_id}, but the "
                f"project currently references ScriptPlan "
                f"{project.script_plan_id}; rerun Visual Planning first"
            )
        if visual_plan.voice_plan_id != voice_plan.id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was "
                f"generated for VoicePlan {visual_plan.voice_plan_id}, but the "
                f"current VoicePlan is {voice_plan.id}; rerun Visual Planning first"
            )

    def _render_all(
        self,
        renderer_input: VisualRendererInput,
        script_plan: ScriptPlan,
        voice_plan: VoicePlan,
        visual_plan: VisualPlan,
    ) -> tuple[VisualRenderManifest, int]:
        project_id = renderer_input.project_id
        beat_by_id = {beat.beat_id: beat for beat in visual_plan.beats}

        # Phase 25: diagram_spec presence/absence is checked against every
        # beat's media_type up front, before anything renders -- mirrors
        # Phase 24's dependency-graph validation timing.
        _validate_diagram_specs(visual_plan)
        # Phase 26: every COMPOSITION beat must have a composition_specs
        # entry, checked at the same upfront point.
        _validate_composition_specs(visual_plan, renderer_input)

        # Phase 24 (generalized in Phase 26 to also cover COMPOSITION layer
        # source_beat_id references): the full beat-to-beat dependency graph
        # is built and topologically ordered up front -- before any beat
        # renders, any provider is called, or any ModuleRun/file write
        # happens. An unknown reference, a self-reference, or a cycle fails
        # here, not partway through rendering.
        edges = _build_background_dependency_edges(visual_plan, renderer_input)
        render_order = _topological_beat_order(visual_plan.beats, edges)

        assets: list[RenderedVisualAsset] = []
        requirements: list[VisualRenderRequirement] = []
        provider_call_count = 0
        rendered_assets_by_beat_id: dict[str, RenderedVisualAsset] = {}
        requirements_by_beat_id: dict[str, VisualRenderRequirement] = {}

        for beat_id in render_order:
            beat = beat_by_id[beat_id]
            if beat.media_type in _PROVIDER_RENDERABLE_MEDIA_TYPES:
                asset, calls_made = self._render_one_beat(project_id, visual_plan.id, beat)
                provider_call_count += calls_made
                assets.append(asset)
                rendered_assets_by_beat_id[beat.beat_id] = asset
            elif beat.media_type is VisualMediaType.DIAGRAM:
                # Phase 25: never a provider call -- rendered deterministically
                # and locally by DiagramRenderer instead (_render_diagram_beat).
                diagram_asset = self._render_diagram_beat(project_id, visual_plan.id, beat)
                assets.append(diagram_asset)
                rendered_assets_by_beat_id[beat.beat_id] = diagram_asset
            elif beat.media_type is VisualMediaType.COMPOSITION:
                # Phase 26: never a provider call -- composites other beats'
                # already-rendered outputs via VisualLayerCompositor instead.
                composition_asset = self._render_composition_beat(
                    project_id,
                    visual_plan.id,
                    beat,
                    renderer_input,
                    rendered_assets_by_beat_id,
                    requirements_by_beat_id,
                )
                assets.append(composition_asset)
                rendered_assets_by_beat_id[beat.beat_id] = composition_asset
            elif beat.media_type is VisualMediaType.TI_STATE:
                # Never a provider call, in either mode -- see this
                # module's docstring and _render_ti_state_beat.
                ti_result = self._render_ti_state_beat(
                    project_id, visual_plan.id, beat, renderer_input, rendered_assets_by_beat_id
                )
                if isinstance(ti_result, RenderedVisualAsset):
                    assets.append(ti_result)
                    rendered_assets_by_beat_id[beat.beat_id] = ti_result
                else:
                    requirements.append(ti_result)
                    requirements_by_beat_id[beat.beat_id] = ti_result
            elif beat.media_type in _REUSE_ONLY_MEDIA_TYPES:
                requirement = _reuse_only_requirement(beat)
                requirements.append(requirement)
                requirements_by_beat_id[beat.beat_id] = requirement
            else:
                requirement = _external_required_requirement(beat)
                requirements.append(requirement)
                requirements_by_beat_id[beat.beat_id] = requirement

        manifest = VisualRenderManifest(
            script_plan_id=script_plan.id,
            voice_plan_id=voice_plan.id,
            visual_plan_id=visual_plan.id,
            provider=self._visual_settings.provider,
            output_format=self._visual_settings.output_format,
            assets=assets,
            requirements=requirements,
            created_at=datetime.now(timezone.utc),
        )
        return manifest, provider_call_count

    def _render_ti_state_beat(
        self,
        project_id: UUID,
        visual_plan_id: UUID,
        beat: VisualBeat,
        renderer_input: VisualRendererInput,
        rendered_assets_by_beat_id: dict[str, RenderedVisualAsset],
    ) -> RenderedVisualAsset | VisualRenderRequirement:
        """Phase 23's deterministic TI_STATE routing, extended by Phase 24
        for background_beat_id. Never calls a VisualProvider -- resolves
        and, only when a background was explicitly supplied (by either
        source), composites a canonical Tí asset instead."""
        if beat.ti_state is None:
            raise InvalidVisualBeatError(
                f"VisualBeat {beat.beat_id!r} is TI_STATE but has no ti_state set"
            )
        if self._ti_compositor is None:
            raise TiCompositorNotConfiguredError(
                f"VisualBeat {beat.beat_id!r} is TI_STATE but this VisualRenderer "
                f"was constructed without a TiCompositor"
            )

        resolved_ti_state = to_ti_state(beat.ti_state)
        source = renderer_input.ti_state_sources.get(beat.beat_id, TiStateSource())

        if source.mode is TiStateRenderMode.STANDALONE:
            return self._standalone_ti_state_requirement(beat, resolved_ti_state)

        # TiStateSource's own validator already guarantees exactly one of
        # background_path/background_beat_id is set in COMPOSITE mode --
        # nothing left to invent or default here.
        background_path = self._resolve_composite_background_path(
            beat, source, rendered_assets_by_beat_id
        )

        return self._composite_ti_state_asset(
            project_id, visual_plan_id, beat, resolved_ti_state, source, background_path
        )

    def _resolve_composite_background_path(
        self,
        beat: VisualBeat,
        source: TiStateSource,
        rendered_assets_by_beat_id: dict[str, RenderedVisualAsset],
    ) -> Path:
        """Resolves TI_STATE COMPOSITE's single background source. Phase 23's
        background_path is returned as-is (unchanged behavior). Phase 24's
        background_beat_id is resolved from THIS render pass's own results
        -- the background beat_id renders before this beat by construction
        (see _topological_beat_order), so rendered_assets_by_beat_id always
        already holds it when this beat is one that produces a
        RenderedVisualAsset for it; never re-rendered, never regenerated,
        never copied -- the path is handed to TiCompositor unchanged."""
        if source.background_path is not None:
            return source.background_path

        background_beat_id = source.background_beat_id
        assert background_beat_id is not None  # enforced by TiStateSource validation

        background_asset = rendered_assets_by_beat_id.get(background_beat_id)
        if background_asset is None:
            raise BackgroundBeatNotRenderedError(
                f"VisualBeat {beat.beat_id!r} names background_beat_id "
                f"{background_beat_id!r}, but that beat produced no "
                f"RenderedVisualAsset in this render pass -- it resolved to a "
                f"requirement instead (REUSE_ONLY/EXTERNAL_REQUIRED/standalone "
                f"CANONICAL_ASSET_READY beats can never serve as a background)"
            )

        resolved_path = self._visual_store.root / background_asset.file_path
        if not resolved_path.is_file():
            raise BackgroundBeatAssetMissingError(
                f"VisualBeat {beat.beat_id!r} names background_beat_id "
                f"{background_beat_id!r}, whose RenderedVisualAsset points at "
                f"{resolved_path}, but that file does not exist on disk"
            )
        return resolved_path

    def _standalone_ti_state_requirement(
        self, beat: VisualBeat, resolved_ti_state: TiState
    ) -> VisualRenderRequirement:
        """Mode A: no background compositing. Resolves the exact canonical
        TiAsset via the injected TiCompositor's TiAssetRetriever and
        records it as CANONICAL_ASSET_READY -- reference points directly at
        the canonical asset's own relative_path (which already encodes
        asset-set id, version, and state per app/ti_assets/storage.py's
        {asset_set_id}/{version}/{state}.ext convention), so a future
        editor/assembly pass has everything it needs to place Tí without
        this renderer composing anything itself."""
        asset = self._ti_compositor.retriever.get_asset(resolved_ti_state)
        active_set = self._ti_compositor.retriever.get_active_asset_set()
        return VisualRenderRequirement(
            beat_id=beat.beat_id,
            media_type=VisualMediaType.TI_STATE,
            status=VisualRequirementStatus.CANONICAL_ASSET_READY,
            reference=asset.relative_path,
            resolved_ti_state=resolved_ti_state,
            notes=(
                f"Standalone canonical Tí asset from TiAssetSet {active_set.id} "
                f"version {active_set.version!r}; no background supplied for this beat"
            ),
        )

    def _composite_ti_state_asset(
        self,
        project_id: UUID,
        visual_plan_id: UUID,
        beat: VisualBeat,
        resolved_ti_state: TiState,
        source: TiStateSource,
        background_path: Path,
    ) -> RenderedVisualAsset:
        """Mode B: composite the canonical Tí asset onto a background via
        TiCompositor -- an explicit background_path (Phase 23) or another
        beat's own rendered output resolved by
        _resolve_composite_background_path (Phase 24); this method treats
        both identically once resolved to a path. Output is always PNG
        regardless of this renderer's own VisualSettings.output_format --
        TiCompositor enforces PNG-only output itself (Phase 22:
        deterministic lossless composition), so forcing '.png' here keeps
        the manifest's file_path truthful rather than silently mismatched."""
        render_job_id = f"{beat.beat_id}_R1"
        relative_path = f"{project_id}/{visual_plan_id}/{render_job_id}.png"
        output_path = self._visual_store.root / relative_path

        request = TiCompositeRequest(
            background_path=background_path,
            output_path=output_path,
            placement=source.placement or _DEFAULT_TI_PLACEMENT,
            ti_state=resolved_ti_state,
        )
        # No retry: TiCompositor is a deterministic local operation, not a
        # provider call -- there is nothing transient to retry, and this
        # renderer's retry budget (_render_with_retry) is reserved for
        # VisualProviderError only.
        result = self._ti_compositor.composite(request)

        return RenderedVisualAsset(
            render_job_id=render_job_id,
            beat_id=beat.beat_id,
            media_type=VisualMediaType.TI_STATE,
            file_path=relative_path,
            width=result.background_width,
            height=result.background_height,
            resolved_ti_state=resolved_ti_state,
            notes=f"Composited canonical Tí onto background {background_path}",
        )

    def _render_diagram_beat(
        self, project_id: UUID, visual_plan_id: UUID, beat: VisualBeat
    ) -> RenderedVisualAsset:
        """Phase 25's deterministic DIAGRAM routing. Never calls a
        VisualProvider, never retried (DiagramRenderer is a deterministic
        local operation, like TiCompositor -- there is nothing transient to
        retry). `_validate_diagram_specs` has already guaranteed
        `beat.diagram_spec` is set by the time this runs. Output is always
        PNG -- DiagramRenderer's only supported format -- regardless of
        this renderer's own VisualSettings.output_format, for the same
        "keep file_path truthful" reason Phase 23's TI_STATE COMPOSITE
        output is always PNG too."""
        assert beat.diagram_spec is not None  # enforced by _validate_diagram_specs

        render_job_id = f"{beat.beat_id}_R1"
        relative_path = f"{project_id}/{visual_plan_id}/{render_job_id}.png"
        output_path = self._visual_store.root / relative_path

        try:
            result = self._diagram_renderer.render(beat.diagram_spec, output_path)
        except DiagramRenderError:
            # No AI fallback, no substitute generic still -- propagate
            # unchanged so run() records a FAILED ModuleRun.
            raise

        return RenderedVisualAsset(
            render_job_id=render_job_id,
            beat_id=beat.beat_id,
            media_type=VisualMediaType.DIAGRAM,
            file_path=relative_path,
            width=result.width,
            height=result.height,
            notes="Rendered deterministically by DiagramRenderer; no VisualProvider call.",
        )

    def _render_composition_beat(
        self,
        project_id: UUID,
        visual_plan_id: UUID,
        beat: VisualBeat,
        renderer_input: VisualRendererInput,
        rendered_assets_by_beat_id: dict[str, RenderedVisualAsset],
        requirements_by_beat_id: dict[str, VisualRenderRequirement],
    ) -> RenderedVisualAsset:
        """Phase 26's deterministic COMPOSITION routing. Never calls a
        VisualProvider, never retried (VisualLayerCompositor is a
        deterministic local operation, like TiCompositor/DiagramRenderer).
        Every CompositionLayerSource.source_beat_id has already rendered by
        the time this runs (see _topological_beat_order, generalized in
        Phase 26 to include composition_specs references), so every layer
        resolves purely from this same render pass's own results -- no
        re-rendering, no regeneration, no provider fallback."""
        composition_spec = renderer_input.composition_specs.get(beat.beat_id)
        if composition_spec is None:
            raise MissingCompositionSpecError(
                f"VisualBeat {beat.beat_id!r} is COMPOSITION but has no entry in "
                f"VisualRendererInput.composition_specs"
            )

        resolved_layers = [
            self._resolve_composition_layer(
                beat.beat_id, layer_source, rendered_assets_by_beat_id, requirements_by_beat_id
            )
            for layer_source in composition_spec.layers
        ]

        render_job_id = f"{beat.beat_id}_R1"
        relative_path = f"{project_id}/{visual_plan_id}/{render_job_id}.png"
        output_path = self._visual_store.root / relative_path

        spec = LayerCompositionSpec(layers=resolved_layers, output_path=output_path)
        try:
            result = self._layer_compositor.compose(spec)
        except LayerCompositionError:
            # No AI fallback, no substitute generic still, no silently
            # dropped layer -- propagate unchanged so run() records a
            # FAILED ModuleRun.
            raise

        source_beat_ids = [
            layer_source.source_beat_id
            for layer_source in composition_spec.layers
            if layer_source.source_beat_id is not None
        ]
        return RenderedVisualAsset(
            render_job_id=render_job_id,
            beat_id=beat.beat_id,
            media_type=VisualMediaType.COMPOSITION,
            file_path=relative_path,
            width=result.canvas_width,
            height=result.canvas_height,
            notes=(
                f"Composited from source beat(s) {source_beat_ids} via "
                f"VisualLayerCompositor; no VisualProvider call."
            ),
        )

    def _resolve_composition_layer(
        self,
        composition_beat_id: str,
        layer_source: CompositionLayerSource,
        rendered_assets_by_beat_id: dict[str, RenderedVisualAsset],
        requirements_by_beat_id: dict[str, VisualRenderRequirement],
    ) -> BackgroundLayer | OverlayLayer:
        """Resolves one CompositionLayerSource to a concrete VisualLayer
        (app/layer_compositor/models.py). CompositionLayerSource's own
        validator already guarantees exactly one of source_path/
        source_beat_id is set.

        Broader than TI_STATE's own _resolve_composite_background_path by
        design (Phase 26 requirement): a COMPOSITION layer may resolve to
        EITHER a RenderedVisualAsset from this render pass OR a standalone
        canonical Tí CANONICAL_ASSET_READY requirement -- the latter is
        deliberately valid here even though Phase 24 forbids it for a
        TI_STATE beat's own background (see
        BackgroundBeatNotRenderedError's docstring for why the two rules
        differ)."""
        resolved_path = self._resolve_composition_layer_path(
            composition_beat_id, layer_source, rendered_assets_by_beat_id, requirements_by_beat_id
        )

        if layer_source.source_type is LayerSourceType.BACKGROUND:
            return BackgroundLayer(id=layer_source.id, source_path=resolved_path, notes=layer_source.notes)

        return OverlayLayer(
            id=layer_source.id,
            source_path=resolved_path,
            z_index=layer_source.z_index,
            anchor=layer_source.anchor,
            scale=layer_source.scale,
            margin=layer_source.margin,
            offset_x=layer_source.offset_x,
            offset_y=layer_source.offset_y,
            notes=layer_source.notes,
        )

    def _resolve_composition_layer_path(
        self,
        composition_beat_id: str,
        layer_source: CompositionLayerSource,
        rendered_assets_by_beat_id: dict[str, RenderedVisualAsset],
        requirements_by_beat_id: dict[str, VisualRenderRequirement],
    ) -> Path:
        if layer_source.source_path is not None:
            return layer_source.source_path

        source_beat_id = layer_source.source_beat_id
        assert source_beat_id is not None  # enforced by CompositionLayerSource validation

        rendered_asset = rendered_assets_by_beat_id.get(source_beat_id)
        if rendered_asset is not None:
            resolved_path = self._visual_store.root / rendered_asset.file_path
            if not resolved_path.is_file():
                raise BackgroundBeatAssetMissingError(
                    f"VisualBeat {composition_beat_id!r}'s layer {layer_source.id!r} "
                    f"references source_beat_id {source_beat_id!r}, whose "
                    f"RenderedVisualAsset points at {resolved_path}, but that file "
                    f"does not exist on disk"
                )
            return resolved_path

        requirement = requirements_by_beat_id.get(source_beat_id)
        if requirement is not None and requirement.status is VisualRequirementStatus.CANONICAL_ASSET_READY:
            # A standalone TI_STATE beat -- resolve its canonical Tí asset
            # through the same injected TiCompositor.retriever the beat
            # itself used, never a second lookup path. TiCompositorNotConfiguredError
            # could not have been avoided for that beat to produce this
            # requirement in the first place, so self._ti_compositor is
            # guaranteed non-None here.
            assert self._ti_compositor is not None
            ti_asset = self._ti_compositor.retriever.get_asset(requirement.resolved_ti_state)
            return self._ti_compositor.retriever.resolve_path(ti_asset)

        raise CompositionSourceNotRenderedError(
            f"VisualBeat {composition_beat_id!r}'s layer {layer_source.id!r} references "
            f"source_beat_id {source_beat_id!r}, but that beat produced no usable raster "
            f"output in this render pass (neither a RenderedVisualAsset nor a standalone "
            f"canonical Tí CANONICAL_ASSET_READY requirement)"
        )

    def _render_one_beat(
        self, project_id: UUID, visual_plan_id: UUID, beat: VisualBeat
    ) -> tuple[RenderedVisualAsset, int]:
        render_job_id = f"{beat.beat_id}_R1"
        request = VisualRenderRequest(
            render_job_id=render_job_id,
            beat_id=beat.beat_id,
            media_type=beat.media_type,
            concept=beat.concept,
            primary_focus=beat.primary_focus,
            secondary_elements=list(beat.secondary_elements),
            context_elements=list(beat.context_elements),
            ti_state=beat.ti_state,
            motion_intent=beat.motion_intent,
            reuse_key=beat.reuse_key,
            output_format=self._visual_settings.output_format,
        )

        response, calls_made = self._render_with_retry(request)

        relative_path = _relative_path_for(
            project_id, visual_plan_id, render_job_id, self._visual_settings.output_format
        )
        # No retry on a write failure: a provider call that already
        # succeeded is not repeated just because the disk write after it
        # failed.
        self._visual_store.write(relative_path, response.asset_bytes)

        asset = RenderedVisualAsset(
            render_job_id=render_job_id,
            beat_id=beat.beat_id,
            media_type=beat.media_type,
            file_path=relative_path,
            width=response.width,
            height=response.height,
            provider_request_id=response.provider_request_id,
        )
        return asset, calls_made

    def _render_with_retry(self, request: VisualRenderRequest) -> tuple[VisualRenderResponse, int]:
        """At most 1 + max_provider_retries total attempts. Only
        VisualProviderError is retried -- any other exception propagates
        immediately, unretried."""
        total_attempts = 1 + self._visual_settings.max_provider_retries

        for attempt in range(1, total_attempts + 1):
            try:
                response = self._visual_provider.render(request)
            except VisualProviderError:
                if attempt >= total_attempts:
                    raise
                continue
            return response, attempt

        raise AssertionError("unreachable: _render_with_retry loop exited without a result")


def _relative_path_for(
    project_id: UUID, visual_plan_id: UUID, render_job_id: str, output_format
) -> str:
    return f"{project_id}/{visual_plan_id}/{render_job_id}.{output_format.value.lower()}"


def _validate_diagram_specs(visual_plan: VisualPlan) -> None:
    """Phase 25: structural cross-check between VisualBeat.media_type and
    diagram_spec, enforced here -- like TI_STATE's own `beat.ti_state is
    None` check -- rather than as a VisualBeat model-level cross-field
    validator. VisualBeat stays one shared shape across every media type,
    with per-media-type requiredness enforced only where the field is
    actually consumed."""
    for beat in visual_plan.beats:
        if beat.media_type is VisualMediaType.DIAGRAM:
            if beat.diagram_spec is None:
                raise MissingDiagramSpecError(
                    f"VisualBeat {beat.beat_id!r} is DIAGRAM but has no diagram_spec set"
                )
        elif beat.diagram_spec is not None:
            raise UnexpectedDiagramSpecError(
                f"VisualBeat {beat.beat_id!r} is {beat.media_type.value} but carries a "
                f"diagram_spec -- diagram_spec is only valid for DIAGRAM beats"
            )


def _validate_composition_specs(visual_plan: VisualPlan, renderer_input: VisualRendererInput) -> None:
    """Phase 26: every COMPOSITION beat must have a
    VisualRendererInput.composition_specs entry -- there is no default to
    fall back to (unlike TI_STATE's STANDALONE default), so a missing
    entry fails immediately, before any beat renders."""
    for beat in visual_plan.beats:
        if beat.media_type is VisualMediaType.COMPOSITION and beat.beat_id not in renderer_input.composition_specs:
            raise MissingCompositionSpecError(
                f"VisualBeat {beat.beat_id!r} is COMPOSITION but has no entry in "
                f"VisualRendererInput.composition_specs"
            )


def _iter_beat_to_beat_references(visual_plan: VisualPlan, renderer_input: VisualRendererInput):
    """Yields (referencing_beat_id, referenced_beat_id) for every explicit
    beat-to-beat visual dependency in this render pass: TI_STATE's
    background_beat_id (Phase 24) and COMPOSITION's per-layer
    source_beat_id (Phase 26). The single source of truth
    _build_background_dependency_edges reads from, rather than
    maintaining a second, parallel dependency engine for COMPOSITION."""
    for beat in visual_plan.beats:
        if beat.media_type is VisualMediaType.TI_STATE:
            source = renderer_input.ti_state_sources.get(beat.beat_id)
            if (
                source is not None
                and source.mode is TiStateRenderMode.COMPOSITE
                and source.background_beat_id is not None
            ):
                yield beat.beat_id, source.background_beat_id
        elif beat.media_type is VisualMediaType.COMPOSITION:
            composition_spec = renderer_input.composition_specs.get(beat.beat_id)
            if composition_spec is not None:
                for layer_source in composition_spec.layers:
                    if layer_source.source_beat_id is not None:
                        yield beat.beat_id, layer_source.source_beat_id


def _build_background_dependency_edges(
    visual_plan: VisualPlan, renderer_input: VisualRendererInput
) -> dict[str, set[str]]:
    """Phase 24 (generalized in Phase 26): dependency beat_id -> the set of
    beat_ids that must render after it, derived ONLY from
    _iter_beat_to_beat_references (never from list position,
    script_line_ids, narrative_node, or any other field). Validates each
    reference as it is found -- unknown beat_id and self-reference fail
    here, before any topological ordering or rendering is attempted."""
    known_beat_ids = {beat.beat_id for beat in visual_plan.beats}
    edges: dict[str, set[str]] = {beat_id: set() for beat_id in known_beat_ids}

    for beat_id, referenced_beat_id in _iter_beat_to_beat_references(visual_plan, renderer_input):
        if referenced_beat_id == beat_id:
            raise SelfReferentialBackgroundBeatError(
                f"VisualBeat {beat_id!r} names itself as its own source beat_id"
            )
        if referenced_beat_id not in known_beat_ids:
            raise UnknownBackgroundBeatError(
                f"VisualBeat {beat_id!r} references unknown beat_id "
                f"{referenced_beat_id!r} -- no beat with that id exists in this VisualPlan"
            )
        edges[referenced_beat_id].add(beat_id)

    return edges


def _topological_beat_order(beats: list[VisualBeat], edges: dict[str, set[str]]) -> list[str]:
    """A stable topological order over beats' own beat_ids: whenever more
    than one beat is currently ready to render (in-degree zero), the one
    appearing earliest in the authored VisualPlan.beats list is chosen
    next. This is what guarantees the authored order is preserved exactly
    wherever background_beat_id does not force a beat later, while still
    guaranteeing every dependency (background beat) renders before its
    dependent. Plain Kahn's algorithm with an authored-index tiebreak --
    not a general workflow/orchestrator framework."""
    order_index = {beat.beat_id: i for i, beat in enumerate(beats)}
    beat_ids = list(order_index)

    indegree = {beat_id: 0 for beat_id in beat_ids}
    for dependents in edges.values():
        for dependent_id in dependents:
            indegree[dependent_id] += 1

    remaining = set(beat_ids)
    result: list[str] = []
    while remaining:
        ready = sorted(
            (beat_id for beat_id in remaining if indegree[beat_id] == 0),
            key=lambda beat_id: order_index[beat_id],
        )
        if not ready:
            cyclic = sorted(remaining, key=lambda beat_id: order_index[beat_id])
            raise BackgroundBeatCycleError(
                f"background_beat_id dependencies form a cycle among beats: {cyclic}"
            )
        next_id = ready[0]
        result.append(next_id)
        remaining.discard(next_id)
        for dependent_id in edges.get(next_id, ()):
            indegree[dependent_id] -= 1

    return result


def _reuse_only_requirement(beat: VisualBeat) -> VisualRenderRequirement:
    """ASSET_REUSE never calls a VisualProvider; reuse_key is the reference.
    TI_STATE no longer routes through here as of Phase 23 -- it has its own
    dedicated canonical-resolution/compositing path
    (VisualRenderer._render_ti_state_beat), since it can never again be
    satisfied by an arbitrary reuse_key placeholder now that a real
    canonical asset always exists for every state."""
    if not beat.reuse_key or not beat.reuse_key.strip():
        raise InvalidVisualBeatError(
            f"VisualBeat {beat.beat_id!r} is ASSET_REUSE but has a blank reuse_key"
        )

    return VisualRenderRequirement(
        beat_id=beat.beat_id,
        media_type=beat.media_type,
        status=VisualRequirementStatus.REUSE_ONLY,
        reference=beat.reuse_key,
    )


def _external_required_requirement(beat: VisualBeat) -> VisualRenderRequirement:
    """LIMITED_MOTION, EVIDENCE_MEDIA, and AI_HERO_VIDEO never call a
    VisualProvider in Phase 19 -- a future runtime execution path must
    supply them. Evidence source ids and motion intent are preserved so
    that future path has what it needs, without downloading or generating
    anything here."""
    if beat.media_type is VisualMediaType.EVIDENCE_MEDIA:
        reference = ",".join(beat.evidence_source_ids) if beat.evidence_source_ids else None
        notes = "EVIDENCE_MEDIA requires a future external evidence-sourcing pass; no web access performed here."
    elif beat.media_type is VisualMediaType.LIMITED_MOTION:
        reference = beat.reuse_key
        notes = (
            "LIMITED_MOTION requires a future limited-animation rendering pass; "
            f"motion_intent={beat.motion_intent!r}"
        )
    else:  # AI_HERO_VIDEO
        reference = beat.reuse_key
        notes = "AI_HERO_VIDEO requires a future real video-generation provider integration."

    return VisualRenderRequirement(
        beat_id=beat.beat_id,
        media_type=beat.media_type,
        status=VisualRequirementStatus.EXTERNAL_REQUIRED,
        reference=reference,
        notes=notes,
    )


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
