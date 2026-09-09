"""Timeline-Builder-specific domain errors.

Local to this package, like every renderer's own errors.py -- not shared
with app/renderers/visual/errors.py or app/renderers/voice/errors.py, even
though several of these mirror an equivalent class there (e.g. every
Stale*Error here checks a distinct upstream freshness relationship).
"""

from __future__ import annotations


class RendererStateError(Exception):
    """Raised when the builder is run against a project in the wrong state."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the builder cannot load a valid ScriptPlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the builder cannot load a valid VoicePlan artifact."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the builder cannot load a valid VisualPlan artifact."""


class MissingAssemblyPlanArtifactError(Exception):
    """Raised when the builder cannot load a valid AssemblyPlan artifact."""


class MissingVoiceRenderManifestArtifactError(Exception):
    """Raised when the builder cannot load a valid VoiceRenderManifest
    artifact -- VoiceRenderer must run before timeline assembly."""


class MissingVisualRenderManifestArtifactError(Exception):
    """Raised when the builder cannot load a valid VisualRenderManifest
    artifact -- VisualRenderer must run before timeline assembly."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references."""


class StaleVisualPlanError(Exception):
    """Raised when the current VisualPlan was generated for a ScriptPlan or
    VoicePlan other than the ones currently in force."""


class StaleAssemblyPlanError(Exception):
    """Raised when the current AssemblyPlan was generated for a ScriptPlan/
    VoicePlan/VisualPlan combination other than the ones currently in
    force."""


class StaleVoiceRenderManifestError(Exception):
    """Raised when the current VoiceRenderManifest was rendered for a
    ScriptPlan/VoicePlan other than the ones currently in force -- the
    voice audio on disk may not match the current script/delivery plan."""


class StaleVisualRenderManifestError(Exception):
    """Raised when the current VisualRenderManifest was rendered for a
    ScriptPlan/VoicePlan/VisualPlan other than the ones currently in
    force -- the visual assets on disk may not match the current plans."""


class UnknownVoiceChunkReferenceError(Exception):
    """Raised when an AssemblySegment references a voice_chunk_id that does
    not exist in the current VoicePlan. No fuzzy matching."""


class UnknownVisualBeatReferenceError(Exception):
    """Raised when an AssemblySegment references a visual_beat_id that does
    not exist in the current VisualPlan. No fuzzy matching."""


class UnknownMotionSegmentReferenceError(Exception):
    """Raised when TimelineBuilderInput.visual_motions (Phase 29) names a
    segment_id that does not exist in the current AssemblyPlan.segments.
    No fuzzy matching, no nearest-segment fallback."""


class MissingRenderedVoiceTakeError(Exception):
    """Raised when a referenced VoiceChunk has no take_number=1
    RenderedVoiceTake in the current VoiceRenderManifest -- narration
    audio for that chunk was never actually rendered."""


class UnresolvableAudioDurationError(Exception):
    """Raised when a RenderedVoiceTake's exact duration cannot be
    determined: no duration_seconds was recorded AND the audio file is not
    a WAV file this builder can measure deterministically from its header
    (see app/renderers/timeline/builder.py's _measure_wav_duration_ms).
    Never estimated from text length, never guessed."""


class MissingAudioFileError(Exception):
    """Raised when a resolved RenderedVoiceTake's file_path does not
    actually exist under AudioFileStore.root."""


class MissingVisualReferenceError(Exception):
    """Raised when a referenced VisualBeat has neither a RenderedVisualAsset
    nor a VisualRenderRequirement in the current VisualRenderManifest --
    VisualRenderer's own manifest coverage guarantee (every VisualPlan beat
    is covered) should make this unreachable in practice; guarded
    defensively rather than assumed."""


class MissingVisualFileError(Exception):
    """Raised when a resolved RenderedVisualAsset's file_path does not
    actually exist under VisualFileStore.root."""


class TimelineManifestIntegrityError(Exception):
    """Raised when a freshly built TimelineManifest fails its own
    deterministic integrity check (duplicate segment ids, a timeline gap/
    overlap, an out-of-bounds cue timestamp). This indicates an internal
    builder construction bug, not a correctable input error -- there is no
    LLM here and no correction-retry step; the failure is raised directly."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(f"TimelineManifest failed integrity validation: {issues}")
