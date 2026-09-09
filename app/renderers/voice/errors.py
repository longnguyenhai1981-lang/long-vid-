"""Voice-Renderer-specific domain errors.

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


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references (VoicePlan.
    script_plan_id != Project.script_plan_id). A real production-integrity
    gate, checked before any provider call, ModuleRun, or file write --
    voice rendering must never be built on a stale delivery plan."""


class VoiceRenderManifestIntegrityError(Exception):
    """Raised when a freshly built VoiceRenderManifest fails its own
    deterministic integrity check. This indicates an internal renderer
    construction bug, not an LLM-correctable issue -- there is no LLM here
    and no correction-retry step; the failure is raised directly."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(f"VoiceRenderManifest failed integrity validation: {issues}")
