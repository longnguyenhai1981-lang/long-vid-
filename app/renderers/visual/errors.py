"""Visual-Renderer-specific domain errors.

Local to this package, like every engine's own errors.py -- not imported
from app.engines.errors. The renderer lives outside app/engines/ on
purpose (see app/renderers/__init__.py) and does not depend on it.
"""

from __future__ import annotations


class RendererStateError(Exception):
    """Raised when the renderer is run against a project in the wrong state."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid ScriptPlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid VoicePlan for the
    project (missing artifact, or the artifact does not validate)."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid VisualPlan for the
    project (missing artifact, or the artifact does not validate)."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references (VoicePlan.
    script_plan_id != Project.script_plan_id). Checked before any provider
    call, ModuleRun, or file write."""


class StaleVisualPlanError(Exception):
    """Raised when the current VisualPlan was generated for a ScriptPlan or
    VoicePlan other than the ones currently in force (VisualPlan.
    script_plan_id != Project.script_plan_id, or VisualPlan.voice_plan_id !=
    the current VoicePlan's id). Checked before any provider call, ModuleRun,
    or file write -- visual rendering must never be built on a stale
    delivery plan."""


class InvalidVisualBeatError(Exception):
    """Raised when a VisualBeat cannot be routed at all because required
    business data is missing for its media_type -- e.g. an ASSET_REUSE or
    TI_STATE beat with no usable reuse reference. Not a provider failure,
    not retried."""


class TiCompositorNotConfiguredError(Exception):
    """Raised when a TI_STATE beat is encountered but this VisualRenderer
    was constructed with ti_compositor=None (Phase 23). TI_STATE always
    needs canonical-asset resolution now (standalone or composited) -- there
    is no placeholder fallback, so a misconfigured renderer fails clearly
    here rather than silently reverting to the pre-Phase-23 placeholder
    reference or, worse, calling a VisualProvider."""


class UnknownBackgroundBeatError(Exception):
    """Raised when a beat-to-beat reference names a beat_id that does not
    exist anywhere in the current VisualPlan -- TiStateSource.
    background_beat_id (Phase 24) or a CompositionLayerSource.
    source_beat_id (Phase 26; app/renderers/visual/models.py), both
    resolved through the same shared dependency-graph machinery (see
    _build_background_dependency_edges). No fuzzy matching and no
    nearest-beat fallback -- the reference must be an exact, existing
    beat_id."""


class SelfReferentialBackgroundBeatError(Exception):
    """Raised when a beat-to-beat reference (see UnknownBackgroundBeatError
    for which fields) names the very beat that owns it -- a beat can never
    use its own (not-yet-rendered) output as one of its own sources."""


class BackgroundBeatCycleError(Exception):
    """Raised when beat-to-beat references among a VisualPlan's beats (see
    UnknownBackgroundBeatError for which fields feed this graph) form a
    cycle, direct or indirect. Detected from the full dependency graph
    before any beat in the plan renders -- no partial rendering happens
    once a cycle is found."""


class BackgroundBeatNotRenderedError(Exception):
    """Raised when a TiStateSource.background_beat_id (Phase 24) names a
    real VisualPlan beat, but that beat resolved to a VisualRenderRequirement
    instead of a RenderedVisualAsset in this render pass -- e.g. it is
    REUSE_ONLY, EXTERNAL_REQUIRED, or a standalone CANONICAL_ASSET_READY
    TI_STATE beat. Only a beat that actually produced a rendered asset file
    may serve as a TI_STATE background; this renderer never generates or
    repairs a missing one. NOTE: a COMPOSITION layer's source_beat_id
    (Phase 26) is deliberately NOT held to this same restriction -- a
    standalone CANONICAL_ASSET_READY TI_STATE beat IS a valid COMPOSITION
    overlay source (see CompositionSourceNotRenderedError instead)."""


class MissingDiagramSpecError(Exception):
    """Raised when a VisualBeat is media_type=DIAGRAM but diagram_spec is
    None (Phase 25). Checked up front, before any beat in the plan
    renders -- mirrors TI_STATE's own `beat.ti_state is None` check
    (InvalidVisualBeatError), which is likewise enforced where the field
    is consumed rather than as a VisualBeat model-level cross-field
    validator."""


class UnexpectedDiagramSpecError(Exception):
    """Raised when a VisualBeat is NOT media_type=DIAGRAM but carries a
    diagram_spec anyway (Phase 25). diagram_spec is only ever consumed for
    a DIAGRAM beat; a non-DIAGRAM beat authored with one indicates a
    VisualPlan authoring bug, caught here before any rendering starts."""


class BackgroundBeatAssetMissingError(Exception):
    """Raised when a TiStateSource.background_beat_id (Phase 24) resolves to
    a real RenderedVisualAsset, but the file its file_path names does not
    actually exist under VisualFileStore.root. Never regenerates, repairs,
    or substitutes another file for the missing one."""


class MissingCompositionSpecError(Exception):
    """Raised when a VisualBeat is media_type=COMPOSITION but has no entry
    in VisualRendererInput.composition_specs (Phase 26). Unlike TI_STATE's
    STANDALONE default, there is no sensible "compose nothing" default --
    a COMPOSITION beat always needs an explicit CompositionSpec."""


class CompositionSourceNotRenderedError(Exception):
    """Raised when a CompositionLayerSource.source_beat_id (Phase 26)
    names a real VisualPlan beat, but that beat produced no usable raster
    output in this render pass: neither a RenderedVisualAsset nor a
    standalone canonical Tí CANONICAL_ASSET_READY requirement (the two
    kinds a COMPOSITION layer may consume -- see
    BackgroundBeatNotRenderedError's note for why this is broader than
    TI_STATE's own background-source rule). A REUSE_ONLY or
    EXTERNAL_REQUIRED beat, for example, triggers this. Never generates or
    repairs a missing layer source."""


class VisualRenderManifestIntegrityError(Exception):
    """Raised when a freshly built VisualRenderManifest fails its own
    deterministic integrity check. This indicates an internal renderer
    construction bug, not an LLM-correctable issue -- there is no LLM here
    and no correction-retry step; the failure is raised directly."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(f"VisualRenderManifest failed integrity validation: {issues}")
