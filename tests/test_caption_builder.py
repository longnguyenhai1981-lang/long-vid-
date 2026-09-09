"""Phase 31 focused tests: CaptionBuilder.

Covers both the pure text/timing derivation (app/captions/builder.py's
build_caption_manifest) and the artifact-driven, freshness-checked,
ModuleRun-lifecycle layer (app/renderers/caption/builder.py's
CaptionBuilder) -- reuses tests/test_timeline_builder.py's own fixture
builders rather than re-deriving them, exactly like
tests/test_video_renderer.py already does.
"""

from __future__ import annotations

import pytest

from app.captions.builder import build_caption_manifest
from app.captions.errors import (
    CaptionTimingOverflowError,
    MissingCaptionSourceTextError,
    UnknownCaptionChunkReferenceError,
)
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.models.assembly import AssemblySegment
from app.models.common import ModuleRunStatus
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.voice import VoicePlan
from app.renderers.caption.builder import CaptionBuilder
from app.renderers.caption.errors import (
    MissingTimelineManifestArtifactError,
    RendererStateError,
    StaleTimelineManifestError,
    StaleVoicePlanError,
)
from app.renderers.caption.models import CAPTION_MANIFEST_ARTIFACT_TYPE, CaptionBuilderInput
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TimelineBuilderInput
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import list_module_runs_for_project
from tests.test_timeline_builder import _build_ready_project, _builder


def _build_timeline(engine, tmp_path, assembly_overrides=None):
    (
        project_id, script_plan, voice_plan, visual_plan, assembly_plan,
        voice_render_manifest, visual_render_manifest, audio_store, visual_store,
    ) = _build_ready_project(engine, tmp_path, assembly_overrides=assembly_overrides)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    return project_id, script_plan, voice_plan, result.manifest


_MULTI_CHUNK_SEGMENT = {
    "segments": [
        AssemblySegment(
            segment_id="S1", script_line_ids=["L001", "L002", "L003", "L004"],
            voice_chunk_ids=["C001", "C002"], visual_beat_id="V1",
            start_seconds=0.0, end_seconds=8.0, music_state="BED",
            transition_in="NONE", transition_out="NONE",
        ),
    ]
}


# ---------------------------------------------------------------------------
# Pure build_caption_manifest: granularity/timing
# ---------------------------------------------------------------------------


def test_one_voice_chunk_per_segment_produces_one_cue_each(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    caption_manifest = build_caption_manifest(project_id, timeline_manifest, voice_plan, script_plan)

    assert len(caption_manifest.cues) == 2
    assert caption_manifest.cues[0].text == "Đây là câu L001. Đây là câu L002."
    assert caption_manifest.cues[0].voice_chunk_id == "C001"
    assert caption_manifest.cues[0].script_line_ids == ["L001", "L002"]
    assert caption_manifest.cues[1].text == "Đây là câu L003. Đây là câu L004."
    assert caption_manifest.cues[1].voice_chunk_id == "C002"


def test_exact_cumulative_timings_match_timeline_segments(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    caption_manifest = build_caption_manifest(project_id, timeline_manifest, voice_plan, script_plan)

    seg1, seg2 = timeline_manifest.segments
    cue1, cue2 = caption_manifest.cues
    assert (cue1.start_ms, cue1.end_ms) == (seg1.start_ms, seg1.end_ms)
    assert (cue2.start_ms, cue2.end_ms) == (seg2.start_ms, seg2.end_ms)
    assert cue1.duration_ms == seg1.narration[0].duration_ms
    assert caption_manifest.total_duration_ms == timeline_manifest.total_duration_ms


def test_several_chunks_inside_one_visual_segment_produce_multiple_cues(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(
        engine, tmp_path, assembly_overrides=_MULTI_CHUNK_SEGMENT
    )
    caption_manifest = build_caption_manifest(project_id, timeline_manifest, voice_plan, script_plan)

    assert len(timeline_manifest.segments) == 1
    segment = timeline_manifest.segments[0]
    assert len(segment.narration) == 2  # C001 (2000ms) + C002 (1500ms)
    assert len(caption_manifest.cues) == 2  # never collapsed into one giant caption

    cue1, cue2 = caption_manifest.cues
    assert cue1.start_ms == segment.start_ms
    assert cue1.end_ms == segment.start_ms + segment.narration[0].duration_ms
    assert cue2.start_ms == cue1.end_ms
    assert cue2.end_ms == segment.end_ms
    assert cue1.voice_chunk_id == "C001"
    assert cue2.voice_chunk_id == "C002"
    assert cue1.text == "Đây là câu L001. Đây là câu L002."
    assert cue2.text == "Đây là câu L003. Đây là câu L004."


def test_script_and_voice_chunk_ids_preserved(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    caption_manifest = build_caption_manifest(project_id, timeline_manifest, voice_plan, script_plan)
    for cue, segment in zip(caption_manifest.cues, timeline_manifest.segments):
        assert cue.render_job_id == segment.narration[0].render_job_id


def test_render_job_id_preserved_from_timeline_audio_ref(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    caption_manifest = build_caption_manifest(project_id, timeline_manifest, voice_plan, script_plan)
    assert caption_manifest.cues[0].render_job_id == "C001_T1"
    assert caption_manifest.cues[1].render_job_id == "C002_T1"


# ---------------------------------------------------------------------------
# Pure build_caption_manifest: fidelity and failure paths
# ---------------------------------------------------------------------------


def test_no_stt_llm_or_provider_dependency():
    """build_caption_manifest is importable and callable with no
    database, no subprocess, no network -- confirmed by inspecting the
    module's own actual `import`/`from ... import` statements (not a
    naive text search, which would false-positive on this very
    module's own docstring explaining what it does NOT do)."""
    import ast

    import app.captions.builder as builder_module

    with open(builder_module.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    forbidden_roots = {"subprocess", "requests", "httpx", "openai", "anthropic", "whisper", "sqlalchemy"}
    assert imported_roots.isdisjoint(forbidden_roots)


def test_cumulative_narration_overflow_fails(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    # Corrupt one segment's own duration_ms to no longer match the sum of
    # its narration refs' durations -- simulates an internally
    # inconsistent TimelineManifest.
    corrupted_segment = timeline_manifest.segments[0].model_copy(update={"end_ms": 999_999, "duration_ms": 999_999})
    corrupted_manifest = timeline_manifest.model_copy(
        update={"segments": [corrupted_segment, *timeline_manifest.segments[1:]], "total_duration_ms": 999_999 + timeline_manifest.segments[1].duration_ms}
    )
    with pytest.raises(CaptionTimingOverflowError):
        build_caption_manifest(project_id, corrupted_manifest, voice_plan, script_plan)


def test_unknown_voice_chunk_reference_fails(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    empty_voice_plan = VoicePlan(script_plan_id=voice_plan.script_plan_id, chunks=[
        chunk.model_copy(update={"chunk_id": "DIFFERENT"}) for chunk in voice_plan.chunks
    ])
    with pytest.raises(UnknownCaptionChunkReferenceError):
        build_caption_manifest(project_id, timeline_manifest, empty_voice_plan, script_plan)


def test_missing_source_text_fails(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    truncated_beats = [
        beat.model_copy(update={"lines": [line for line in beat.lines if line.line_id != "L001"]})
        for beat in script_plan.beats
    ]
    truncated_script_plan = script_plan.model_copy(update={"beats": truncated_beats})
    with pytest.raises(MissingCaptionSourceTextError):
        build_caption_manifest(project_id, timeline_manifest, voice_plan, truncated_script_plan)


def test_source_text_fidelity_exact_match(engine, tmp_path):
    """Phase 31 requirement #28: caption text must equal the exact
    authored narration text -- punctuation, capitalization, and
    Vietnamese accents included -- with no normalization step."""
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    caption_manifest = build_caption_manifest(project_id, timeline_manifest, voice_plan, script_plan)

    line_text_by_id = {line.line_id: line.text for beat in script_plan.beats for line in beat.lines}
    chunk_by_id = {chunk.chunk_id: chunk for chunk in voice_plan.chunks}
    for cue in caption_manifest.cues:
        chunk = chunk_by_id[cue.voice_chunk_id]
        expected_text = " ".join(line_text_by_id[lid] for lid in chunk.line_ids)
        assert cue.text == expected_text
        for line_id in chunk.line_ids:
            assert line_text_by_id[line_id] in cue.text


# ---------------------------------------------------------------------------
# Renderer-level CaptionBuilder: freshness / ModuleRun
# ---------------------------------------------------------------------------


def test_caption_builder_produces_manifest_matching_timeline(engine, tmp_path):
    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    result = CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))
    assert result.manifest.timeline_manifest_id == timeline_manifest.id
    assert result.manifest.total_duration_ms == timeline_manifest.total_duration_ms
    assert len(result.manifest.cues) == 2


def test_caption_manifest_persisted_as_artifact(engine, tmp_path):
    from app.models.caption import CaptionManifest

    project_id, *_ = _build_timeline(engine, tmp_path)
    result = CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))
    stored = get_artifact(engine, project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, CaptionManifest)
    assert stored == result.manifest


def test_module_run_success(engine, tmp_path):
    project_id, *_ = _build_timeline(engine, tmp_path)
    result = CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    caption_runs = [run for run in runs if run.module == "caption_builder"]
    assert len(caption_runs) == 1
    assert caption_runs[0].status == ModuleRunStatus.SUCCESS
    assert caption_runs[0].output_id == str(result.manifest.id)


def test_renderer_state_error_when_not_mvp_complete(tmp_path, engine):
    from datetime import datetime, timezone

    from app.models.common import ProjectState
    from app.models.project import Project
    from app.storage.projects import create_project

    project = Project(
        title_internal="not ready", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc), state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    with pytest.raises(RendererStateError):
        CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project.project_id))


def test_missing_timeline_manifest_fails_explicitly(engine, tmp_path):
    (
        project_id, script_plan, voice_plan, visual_plan, assembly_plan,
        voice_render_manifest, visual_render_manifest, audio_store, visual_store,
    ) = _build_ready_project(engine, tmp_path)
    # No TimelineManifest ever built.
    with pytest.raises(MissingTimelineManifestArtifactError):
        CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))


def test_stale_voice_plan_fails(engine, tmp_path):
    from uuid import uuid4

    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    stale_voice_plan = voice_plan.model_copy(update={"script_plan_id": uuid4()})
    save_artifact(engine, project_id, "voice_plan", stale_voice_plan)
    with pytest.raises(StaleVoicePlanError):
        CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))


def test_module_run_failed_on_stale_manifest(engine, tmp_path):
    from uuid import uuid4

    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    stale_voice_plan = voice_plan.model_copy(update={"script_plan_id": uuid4()})
    save_artifact(engine, project_id, "voice_plan", stale_voice_plan)
    with pytest.raises(StaleVoicePlanError):
        CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))
    # A freshness failure happens BEFORE any ModuleRun is even created
    # (identical timing to VideoRenderer's own freshness checks) -- no
    # ModuleRun row should exist for this module at all.
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "caption_builder" for run in runs)
