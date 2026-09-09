"""TimelineBuilder: turns an already-locked ScriptPlan/VoicePlan/VisualPlan/
AssemblyPlan chain, plus their already-rendered VoiceRenderManifest/
VisualRenderManifest outputs, into one deterministic executable
TimelineManifest -- the final planning-adjacent artifact before real video
encoding (Phase 27).

MVP_COMPLETE -> load+verify ScriptPlan -> load+verify VoicePlan -> verify
VoicePlan freshness against ScriptPlan -> load+verify VisualPlan -> verify
VisualPlan freshness against ScriptPlan/VoicePlan -> load+verify
AssemblyPlan -> verify AssemblyPlan freshness against ScriptPlan/VoicePlan/
VisualPlan -> load+verify VoiceRenderManifest -> verify it matches the
current ScriptPlan/VoicePlan -> load+verify VisualRenderManifest -> verify
it matches the current ScriptPlan/VoicePlan/VisualPlan -> for every
AssemblySegment, in AssemblyPlan's own order, resolve real narration
timing and a real visual reference -> build a TimelineManifest -> validate
its own integrity as defense in depth -> persist the manifest artifact ->
persist ModuleRun.

This module triggers NO rendering of any kind: no VisualProvider call, no
TiCompositor/DiagramRenderer/VisualLayerCompositor call, no TTS call. It
only reads two already-finalized manifests (VoiceRenderManifest,
VisualRenderManifest) and resolves references into them. If a beat or
chunk this builder needs was never actually rendered, that is an explicit
failure (see app/renderers/timeline/errors.py), never a trigger to render
it now.

Timebase (Phase 27 requirement #2): integer milliseconds, chosen
explicitly over AssemblyPlan's own float seconds (Phase 15) because a
final timeline must never accumulate floating-point drift across dozens
of segments, and over frames-per-second because FPS is a final-video-
encoding concern, not a planning-adjacent one -- a future encoding phase
converts these millisecond boundaries to frame numbers against whatever
FPS it renders at, not the other way around.

Voice-driven primary timing (Phase 27 requirement #3): narration duration
is the ONLY source of segment timing. `_resolve_audio_duration_ms` prefers
a RenderedVoiceTake's own recorded `duration_seconds` (Phase 17/18
provider metadata) when present; when absent, it measures the exact
duration from the WAV file's own header (frames / framerate) via Python's
stdlib `wave` module -- the smallest justified dependency is none at all,
since every real TTS provider in this project (GeminiTTSProvider, Phase
18) only ever emits WAV. A non-WAV file with no recorded duration cannot
be measured deterministically here and fails explicitly
(UnresolvableAudioDurationError) -- never estimated from text length,
never guessed.

Script/voice/visual alignment (Phase 27 requirement #4): reused entirely
from AssemblyPlan (Phase 15), not reinvented. AssemblyPlan's own
normalization already guarantees each AssemblySegment.voice_chunk_ids is
exactly the VoiceChunks overlapping its script lines, and its own business
validation already guarantees exactly one AssemblySegment per VisualBeat,
in VisualPlan.beats' own order -- no new contract field was needed on
AssemblyPlan itself. "Multiple narration chunks intentionally sharing one
visual" (requirement #6) is therefore already structurally represented:
one AssemblySegment's voice_chunk_ids may list more than one VoiceChunk,
all played consecutively under that one segment's single visual reference.

Transition downgrade (Phase 27 requirement #10): AssemblyPlan's own
TransitionIntent (Phase 15) has 5 members (CUT, DISSOLVE, MATCH, PUSH,
NONE) -- richer than a static timeline can execute. Every TimelineSegment
carries BOTH the original TransitionIntent (source_transition_in/out, full
fidelity, never discarded) AND a deterministically downgraded
TimelineTransitionType (transition_in/out, restricted to CUT/HOLD/
CROSSFADE) via the fixed _TRANSITION_TYPE_BY_INTENT mapping below: CUT ->
CUT, NONE -> HOLD, DISSOLVE -> CROSSFADE (a crossfade is still just a
description here, not executed), and MATCH/PUSH -> CUT (a motion-based
transition cannot be represented by a static timeline yet; downgrading to
an instantaneous cut is the documented, non-silent MVP behavior until a
future motion-lite phase).

Music/SFX cues (Phase 27 requirements #8/#9): MusicState (Phase 13) has no
explicit "off" member, so cue derivation uses a fixed, deterministic rule:
whenever a segment's music_state differs from the previous segment's (or
this is the first segment), emit one cue matching the new state
(BED -> MUSIC_BED_START, DUCK -> MUSIC_DUCK, LIFT -> MUSIC_LIFT); one
closing MUSIC_BED_END cue is emitted at the timeline's very end if music
was used at all. Each RenderedVoiceTake's sfx_opportunity (when present)
becomes one SFX_TRIGGER cue at its owning segment's start. No audio DSP,
no automatic song discovery, no beat syncing -- every cue is a reference/
timestamp only.
"""

from __future__ import annotations

import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from app.audio.storage import AudioFileStore
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.common import ModuleRunStatus, MusicState, ProjectState, TransitionIntent
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.timeline import (
    TimelineAudioRef,
    TimelineCue,
    TimelineCueType,
    TimelineManifest,
    TimelineSegment,
    TimelineTransitionType,
    TimelineVisualRef,
    TimelineVisualSourceStatus,
    VisualMotionType,
)
from app.models.visual import VisualPlan
from app.models.visual_render import RenderedVisualAsset, VisualRenderManifest, VisualRenderRequirement
from app.models.voice import VoicePlan
from app.renderers.timeline.errors import (
    MissingAssemblyPlanArtifactError,
    MissingAudioFileError,
    MissingRenderedVoiceTakeError,
    MissingScriptPlanArtifactError,
    MissingVisualFileError,
    MissingVisualPlanArtifactError,
    MissingVisualReferenceError,
    MissingVisualRenderManifestArtifactError,
    MissingVoicePlanArtifactError,
    MissingVoiceRenderManifestArtifactError,
    RendererStateError,
    StaleAssemblyPlanError,
    StaleVisualPlanError,
    StaleVisualRenderManifestError,
    StaleVoicePlanError,
    StaleVoiceRenderManifestError,
    UnknownMotionSegmentReferenceError,
    TimelineManifestIntegrityError,
    UnknownVisualBeatReferenceError,
    UnknownVoiceChunkReferenceError,
    UnresolvableAudioDurationError,
)
from app.renderers.timeline.models import (
    TIMELINE_MANIFEST_ARTIFACT_TYPE,
    TimelineBuilderInput,
    TimelineBuilderResult,
)
from app.renderers.timeline.validation import validate_timeline_manifest
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.visual.storage import VisualFileStore

# Duplicated from app.engines.script.models.SCRIPT_PLAN_ARTIFACT_TYPE,
# app.engines.voice_plan.models.VOICE_PLAN_ARTIFACT_TYPE,
# app.engines.visual_plan.models.VISUAL_PLAN_ARTIFACT_TYPE, and
# app.engines.assembly_plan.models.ASSEMBLY_PLAN_ARTIFACT_TYPE rather than
# imported: each of those modules imports app.llm.models (for its own
# TokenUsage-carrying *Result types), and this package must stay free of
# app.llm even transitively -- see app/renderers/visual/renderer.py's
# identical precedent. VOICE_RENDER_MANIFEST_ARTIFACT_TYPE/
# VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE are imported directly instead: their
# source modules (app/renderers/voice/models.py, app/renderers/visual/
# models.py) are themselves renderer-layer and already free of app.llm.
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"
_ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"

MODULE_NAME = "timeline_builder"
MODULE_VERSION = "0.1"

# The take this builder treats as "the" rendered narration for a chunk.
# Nothing in the current architecture records a "selected take" among a
# VoiceChunk's 1-3 renders (RenderedVoiceTake.take_number, Phase 17) -- take
# 1 is used unconditionally, documented here rather than silently assumed.
_SELECTED_TAKE_NUMBER = 1

_TRANSITION_TYPE_BY_INTENT: dict[TransitionIntent, TimelineTransitionType] = {
    TransitionIntent.CUT: TimelineTransitionType.CUT,
    TransitionIntent.NONE: TimelineTransitionType.HOLD,
    TransitionIntent.DISSOLVE: TimelineTransitionType.CROSSFADE,
    TransitionIntent.MATCH: TimelineTransitionType.CUT,
    TransitionIntent.PUSH: TimelineTransitionType.CUT,
}

_MUSIC_CUE_TYPE_BY_STATE: dict[MusicState, TimelineCueType] = {
    MusicState.BED: TimelineCueType.MUSIC_BED_START,
    MusicState.DUCK: TimelineCueType.MUSIC_DUCK,
    MusicState.LIFT: TimelineCueType.MUSIC_LIFT,
}


class TimelineBuilder:
    def __init__(
        self,
        db_engine: Engine,
        audio_store: AudioFileStore,
        visual_store: VisualFileStore,
        *,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._audio_store = audio_store
        self._visual_store = visual_store
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, builder_input: TimelineBuilderInput) -> TimelineBuilderResult:
        project = self._project_repo.get_project(self._db_engine, builder_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"TimelineBuilder requires project state MVP_COMPLETE, got {project.state.value}"
            )

        script_plan = self._load_and_verify_script_plan(project)
        voice_plan = self._load_and_verify_voice_plan(project)
        self._verify_voice_plan_is_fresh(project, voice_plan)
        visual_plan = self._load_and_verify_visual_plan(project)
        self._verify_visual_plan_is_fresh(project, voice_plan, visual_plan)
        assembly_plan = self._load_and_verify_assembly_plan(project)
        self._verify_assembly_plan_is_fresh(project, voice_plan, visual_plan, assembly_plan)
        voice_render_manifest = self._load_and_verify_voice_render_manifest(project)
        self._verify_voice_render_manifest_is_fresh(script_plan, voice_plan, voice_render_manifest)
        visual_render_manifest = self._load_and_verify_visual_render_manifest(project)
        self._verify_visual_render_manifest_is_fresh(
            script_plan, voice_plan, visual_plan, visual_render_manifest
        )

        run_record = ModuleRun(
            project_id=builder_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[
                str(script_plan.id),
                str(voice_plan.id),
                str(visual_plan.id),
                str(assembly_plan.id),
                str(voice_render_manifest.id),
                str(visual_render_manifest.id),
            ],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            manifest = self._build_timeline(
                builder_input.project_id,
                script_plan,
                voice_plan,
                visual_plan,
                assembly_plan,
                voice_render_manifest,
                visual_render_manifest,
                builder_input.visual_motions,
            )

            issues = validate_timeline_manifest(manifest, script_plan, voice_plan, visual_plan)
            if issues:
                raise TimelineManifestIntegrityError(issues)

            self._artifact_repo.save_artifact(
                self._db_engine, builder_input.project_id, TIMELINE_MANIFEST_ARTIFACT_TYPE, manifest
            )
            # No Project reference update, no state transition -- this
            # builder is artifact-driven, not workflow-driven, exactly like
            # every renderer before it.
        except Exception as exc:
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(self._db_engine, _with_success(run_record, manifest.id))

        return TimelineBuilderResult(
            manifest=manifest,
            module_run_id=run_record.run_id,
            segment_count=len(manifest.segments),
            total_duration_ms=manifest.total_duration_ms,
            cue_count=len(manifest.cues),
        )

    # ------------------------------------------------------------------
    # Load + freshness
    # ------------------------------------------------------------------

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
        if voice_plan.script_plan_id != project.script_plan_id:
            raise StaleVoicePlanError(
                f"The current VoicePlan for project {project.project_id} was "
                f"generated for ScriptPlan {voice_plan.script_plan_id}, but the "
                f"project currently references ScriptPlan {project.script_plan_id}"
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
        if visual_plan.script_plan_id != project.script_plan_id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was "
                f"generated for ScriptPlan {visual_plan.script_plan_id}, but the "
                f"project currently references ScriptPlan {project.script_plan_id}"
            )
        if visual_plan.voice_plan_id != voice_plan.id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was "
                f"generated for VoicePlan {visual_plan.voice_plan_id}, but the "
                f"current VoicePlan is {voice_plan.id}"
            )

    def _load_and_verify_assembly_plan(self, project: Project) -> AssemblyPlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan
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
                f"generated for ScriptPlan {assembly_plan.script_plan_id}, but the "
                f"project currently references ScriptPlan {project.script_plan_id}"
            )
        if assembly_plan.voice_plan_id != voice_plan.id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was "
                f"generated for VoicePlan {assembly_plan.voice_plan_id}, but the "
                f"current VoicePlan is {voice_plan.id}"
            )
        if assembly_plan.visual_plan_id != visual_plan.id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was "
                f"generated for VisualPlan {assembly_plan.visual_plan_id}, but the "
                f"current VisualPlan is {visual_plan.id}"
            )

    def _load_and_verify_voice_render_manifest(self, project: Project) -> VoiceRenderManifest:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVoiceRenderManifestArtifactError(
                f"Project {project.project_id} has no valid VoiceRenderManifest artifact "
                f"-- run VoiceRenderer before timeline assembly"
            ) from exc

    def _verify_voice_render_manifest_is_fresh(
        self, script_plan: ScriptPlan, voice_plan: VoicePlan, voice_render_manifest: VoiceRenderManifest
    ) -> None:
        if voice_render_manifest.script_plan_id != script_plan.id:
            raise StaleVoiceRenderManifestError(
                f"The current VoiceRenderManifest was rendered for ScriptPlan "
                f"{voice_render_manifest.script_plan_id}, but the current ScriptPlan "
                f"is {script_plan.id} -- rerun VoiceRenderer first"
            )
        if voice_render_manifest.voice_plan_id != voice_plan.id:
            raise StaleVoiceRenderManifestError(
                f"The current VoiceRenderManifest was rendered for VoicePlan "
                f"{voice_render_manifest.voice_plan_id}, but the current VoicePlan "
                f"is {voice_plan.id} -- rerun VoiceRenderer first"
            )

    def _load_and_verify_visual_render_manifest(self, project: Project) -> VisualRenderManifest:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVisualRenderManifestArtifactError(
                f"Project {project.project_id} has no valid VisualRenderManifest artifact "
                f"-- run VisualRenderer before timeline assembly"
            ) from exc

    def _verify_visual_render_manifest_is_fresh(
        self,
        script_plan: ScriptPlan,
        voice_plan: VoicePlan,
        visual_plan: VisualPlan,
        visual_render_manifest: VisualRenderManifest,
    ) -> None:
        if visual_render_manifest.script_plan_id != script_plan.id:
            raise StaleVisualRenderManifestError(
                f"The current VisualRenderManifest was rendered for ScriptPlan "
                f"{visual_render_manifest.script_plan_id}, but the current ScriptPlan "
                f"is {script_plan.id} -- rerun VisualRenderer first"
            )
        if visual_render_manifest.voice_plan_id != voice_plan.id:
            raise StaleVisualRenderManifestError(
                f"The current VisualRenderManifest was rendered for VoicePlan "
                f"{visual_render_manifest.voice_plan_id}, but the current VoicePlan "
                f"is {voice_plan.id} -- rerun VisualRenderer first"
            )
        if visual_render_manifest.visual_plan_id != visual_plan.id:
            raise StaleVisualRenderManifestError(
                f"The current VisualRenderManifest was rendered for VisualPlan "
                f"{visual_render_manifest.visual_plan_id}, but the current VisualPlan "
                f"is {visual_plan.id} -- rerun VisualRenderer first"
            )

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_timeline(
        self,
        project_id: UUID,
        script_plan: ScriptPlan,
        voice_plan: VoicePlan,
        visual_plan: VisualPlan,
        assembly_plan: AssemblyPlan,
        voice_render_manifest: VoiceRenderManifest,
        visual_render_manifest: VisualRenderManifest,
        visual_motions: dict[str, VisualMotionType],
    ) -> TimelineManifest:
        chunk_ids = {chunk.chunk_id for chunk in voice_plan.chunks}
        beat_ids = {beat.beat_id for beat in visual_plan.beats}
        segment_ids = {segment.segment_id for segment in assembly_plan.segments}
        takes_by_chunk_and_take_number = {
            (take.chunk_id, take.take_number): take for take in voice_render_manifest.renders
        }
        assets_by_beat_id = {asset.beat_id: asset for asset in visual_render_manifest.assets}
        requirements_by_beat_id = {req.beat_id: req for req in visual_render_manifest.requirements}

        # Phase 29: validated up front, before any segment is built --
        # mirrors every other unknown-reference check's timing.
        unknown_motion_segments = sorted(set(visual_motions) - segment_ids)
        if unknown_motion_segments:
            raise UnknownMotionSegmentReferenceError(
                f"TimelineBuilderInput.visual_motions references unknown segment_ids not "
                f"present in the current AssemblyPlan: {unknown_motion_segments}"
            )

        segments: list[TimelineSegment] = []
        cues: list[TimelineCue] = []
        current_ms = 0
        previous_music_state: MusicState | None = None
        music_ever_used = False

        for assembly_segment in assembly_plan.segments:
            self._check_known_references(assembly_segment, chunk_ids, beat_ids)

            narration_refs, selected_takes = self._resolve_narration(
                assembly_segment, takes_by_chunk_and_take_number
            )
            if not narration_refs:
                raise MissingRenderedVoiceTakeError(
                    f"AssemblySegment {assembly_segment.segment_id!r} has no "
                    f"voice_chunk_ids -- a timeline segment cannot be timed without "
                    f"narration audio"
                )

            segment_duration_ms = sum(ref.duration_ms for ref in narration_refs)
            start_ms = current_ms
            end_ms = start_ms + segment_duration_ms

            visual_ref = self._resolve_visual_ref(
                assembly_segment.visual_beat_id, assets_by_beat_id, requirements_by_beat_id
            )

            segment = TimelineSegment(
                segment_id=assembly_segment.segment_id,
                script_line_ids=list(assembly_segment.script_line_ids),
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=segment_duration_ms,
                narration=narration_refs,
                visual=visual_ref,
                transition_in=_TRANSITION_TYPE_BY_INTENT[assembly_segment.transition_in],
                transition_out=_TRANSITION_TYPE_BY_INTENT[assembly_segment.transition_out],
                source_transition_in=assembly_segment.transition_in,
                source_transition_out=assembly_segment.transition_out,
                music_state=assembly_segment.music_state,
                visual_motion=visual_motions.get(assembly_segment.segment_id, VisualMotionType.STATIC),
                notes=assembly_segment.assembly_note,
            )
            segments.append(segment)

            if previous_music_state is None or assembly_segment.music_state != previous_music_state:
                cues.append(
                    TimelineCue(
                        timestamp_ms=start_ms,
                        cue_type=_MUSIC_CUE_TYPE_BY_STATE[assembly_segment.music_state],
                        segment_id=segment.segment_id,
                    )
                )
            previous_music_state = assembly_segment.music_state
            music_ever_used = True

            for take in selected_takes:
                if take.sfx_opportunity:
                    cues.append(
                        TimelineCue(
                            timestamp_ms=start_ms,
                            cue_type=TimelineCueType.SFX_TRIGGER,
                            reference=take.sfx_opportunity,
                            segment_id=segment.segment_id,
                        )
                    )

            current_ms = end_ms

        if music_ever_used:
            cues.append(TimelineCue(timestamp_ms=current_ms, cue_type=TimelineCueType.MUSIC_BED_END))

        return TimelineManifest(
            project_id=project_id,
            script_plan_id=script_plan.id,
            voice_plan_id=voice_plan.id,
            visual_plan_id=visual_plan.id,
            voice_render_manifest_id=voice_render_manifest.id,
            visual_render_manifest_id=visual_render_manifest.id,
            assembly_plan_id=assembly_plan.id,
            total_duration_ms=current_ms,
            segments=segments,
            cues=cues,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _check_known_references(
        assembly_segment: AssemblySegment, chunk_ids: set[str], beat_ids: set[str]
    ) -> None:
        unknown_chunks = [cid for cid in assembly_segment.voice_chunk_ids if cid not in chunk_ids]
        if unknown_chunks:
            raise UnknownVoiceChunkReferenceError(
                f"AssemblySegment {assembly_segment.segment_id!r} references unknown "
                f"voice_chunk_ids not present in the current VoicePlan: {unknown_chunks}"
            )
        if assembly_segment.visual_beat_id not in beat_ids:
            raise UnknownVisualBeatReferenceError(
                f"AssemblySegment {assembly_segment.segment_id!r} references unknown "
                f"visual_beat_id {assembly_segment.visual_beat_id!r}, not present in "
                f"the current VisualPlan"
            )

    def _resolve_narration(
        self,
        assembly_segment: AssemblySegment,
        takes_by_chunk_and_take_number: dict[tuple[str, int], RenderedVoiceTake],
    ) -> tuple[list[TimelineAudioRef], list[RenderedVoiceTake]]:
        narration_refs: list[TimelineAudioRef] = []
        selected_takes: list[RenderedVoiceTake] = []

        for chunk_id in assembly_segment.voice_chunk_ids:
            take = takes_by_chunk_and_take_number.get((chunk_id, _SELECTED_TAKE_NUMBER))
            if take is None:
                raise MissingRenderedVoiceTakeError(
                    f"VoiceChunk {chunk_id!r} (referenced by AssemblySegment "
                    f"{assembly_segment.segment_id!r}) has no take_number="
                    f"{_SELECTED_TAKE_NUMBER} RenderedVoiceTake in the current "
                    f"VoiceRenderManifest"
                )

            audio_path = self._audio_store.root / take.file_path
            if not audio_path.is_file():
                raise MissingAudioFileError(
                    f"RenderedVoiceTake {take.render_job_id!r} points at "
                    f"{audio_path}, but that file does not exist on disk"
                )

            duration_ms = self._resolve_audio_duration_ms(take, audio_path)
            narration_refs.append(
                TimelineAudioRef(
                    chunk_id=chunk_id,
                    render_job_id=take.render_job_id,
                    file_path=take.file_path,
                    duration_ms=duration_ms,
                )
            )
            selected_takes.append(take)

        return narration_refs, selected_takes

    @staticmethod
    def _resolve_audio_duration_ms(take: RenderedVoiceTake, audio_path: Path) -> int:
        """Prefers the provider-reported duration_seconds (Phase 17/18);
        falls back to reading a WAV file's own header when absent. Never
        estimates from text length."""
        if take.duration_seconds is not None:
            return round(take.duration_seconds * 1000)

        if audio_path.suffix.lower() != ".wav":
            raise UnresolvableAudioDurationError(
                f"RenderedVoiceTake {take.render_job_id!r} has no recorded "
                f"duration_seconds and its file format ({audio_path.suffix!r}) "
                f"cannot be measured deterministically here -- only .wav headers "
                f"are read; duration is never estimated from text length"
            )
        try:
            return _measure_wav_duration_ms(audio_path)
        except (wave.Error, EOFError, OSError) as exc:
            raise UnresolvableAudioDurationError(
                f"Failed to measure WAV duration for RenderedVoiceTake "
                f"{take.render_job_id!r} at {audio_path}: {exc}"
            ) from exc

    def _resolve_visual_ref(
        self,
        visual_beat_id: str,
        assets_by_beat_id: dict[str, RenderedVisualAsset],
        requirements_by_beat_id: dict[str, VisualRenderRequirement],
    ) -> TimelineVisualRef:
        asset = assets_by_beat_id.get(visual_beat_id)
        if asset is not None:
            resolved_path = self._visual_store.root / asset.file_path
            if not resolved_path.is_file():
                raise MissingVisualFileError(
                    f"RenderedVisualAsset for VisualBeat {visual_beat_id!r} points at "
                    f"{resolved_path}, but that file does not exist on disk"
                )
            return TimelineVisualRef(
                visual_beat_id=visual_beat_id,
                media_type=asset.media_type,
                status=TimelineVisualSourceStatus.RENDERED,
                file_path=asset.file_path,
                resolved_ti_state=asset.resolved_ti_state,
                width=asset.width,
                height=asset.height,
            )

        requirement = requirements_by_beat_id.get(visual_beat_id)
        if requirement is not None:
            return TimelineVisualRef(
                visual_beat_id=visual_beat_id,
                media_type=requirement.media_type,
                status=TimelineVisualSourceStatus.REQUIREMENT,
                reference=requirement.reference,
                resolved_ti_state=requirement.resolved_ti_state,
            )

        raise MissingVisualReferenceError(
            f"VisualBeat {visual_beat_id!r} has neither a RenderedVisualAsset nor a "
            f"VisualRenderRequirement in the current VisualRenderManifest"
        )


def _measure_wav_duration_ms(path: Path) -> int:
    """Reads only the WAV header (frame count, sample rate) -- never
    decodes audio samples. Deterministic for a fixed file."""
    with wave.open(str(path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frame_rate = wav_file.getframerate()
    if frame_rate <= 0:
        raise UnresolvableAudioDurationError(f"WAV file at {path} reports framerate <= 0")
    return round(frame_count / frame_rate * 1000)


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
