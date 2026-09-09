from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from app.audio.config import TTSSettings
from app.audio.errors import AudioWriteError, TTSProviderError
from app.audio.fake import FakeTTSProvider
from app.audio.models import TTSResponse
from app.audio.storage import AudioFileStore
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.models import SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.models.common import (
    GateEvaluation,
    GateStatus,
    ModuleRunStatus,
    PrimaryPayoff,
    ProjectState,
)
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.audio import VoiceRenderManifest
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.voice.errors import (
    MissingScriptPlanArtifactError,
    MissingVoicePlanArtifactError,
    RendererStateError,
    StaleVoicePlanError,
    VoiceRenderManifestIntegrityError,
)
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRendererInput
from app.renderers.voice.renderer import VoiceRenderer
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.errors import ArtifactNotFoundError
from app.storage.module_runs import list_module_runs_for_project, save_module_run
from app.storage.projects import (
    create_project,
    get_project,
    update_artifact_reference,
    update_project_state,
)


# ---------------------------------------------------------------------------
# Fixture builders (project graph walk, mirroring tests/test_voice_plan_engine.py)
# ---------------------------------------------------------------------------


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]):
    now = datetime.now(timezone.utc)
    save_module_run(
        engine,
        ModuleRun(
            project_id=project_id,
            module=module,
            module_version="0.1",
            started_at=now,
            completed_at=now,
            input_ids=input_ids,
            status=ModuleRunStatus.SUCCESS,
        ),
    )


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01 - Tacoma Narrows",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


def _valid_idea_candidate() -> IdeaCandidate:
    return IdeaCandidate(
        topic="Tacoma Narrows Bridge",
        central_question="Why did a sturdy bridge collapse in mild wind?",
        abt=ABT(
            and_context="Engineers believed the bridge was safe",
            but_complication="It oscillated violently and collapsed in moderate wind",
            therefore_investigation="Investigate the hidden aerodynamic mechanism",
        ),
        primary_payoff=PrimaryPayoff.REVERSAL,
        physics_core="Self-excited aeroelastic flutter",
        audience_prerequisite="none",
        brand_fit=GateEvaluation(status="PASS", reason="ok"),
        general_audience_gate=GateEvaluation(status="PASS", reason="ok"),
        longform_potential=GateEvaluation(status="PASS", reason="ok"),
    )


def _valid_research_r0(idea_id: UUID) -> ResearchR0:
    return ResearchR0(
        idea_id=idea_id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        recommendation="CONTINUE",
    )


def _valid_feasibility_report() -> FeasibilityReport:
    return FeasibilityReport(
        status="PASS",
        audience=SubEvaluation(status="PASS", reason="r"),
        science=SubEvaluation(status="PASS", reason="r"),
        narrative=SubEvaluation(status="PASS", reason="r"),
        visual=SubEvaluation(status="PASS", reason="r"),
        production=ProductionEvaluation(status="PASS", estimated_complexity="LOW", reason="r"),
    )


def _valid_research_package(central_question: str) -> ResearchPackage:
    return ResearchPackage(
        central_question=central_question,
        executive_summary="Flutter caused the bridge to collapse.",
        physics_core="Self-excited aeroelastic flutter",
        simplification_boundary="1. safe_model: ... 2. allowed_simplifications: ...",
        claims=[
            Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")
        ],
    )


def _valid_narrative_plan(central_question: str) -> NarrativePlan:
    return NarrativePlan(
        central_question=central_question,
        scqa=SCQA(
            situation="A bridge opened to fanfare",
            complication="It oscillated wildly in ordinary wind",
            question="Why would this happen?",
            answer="Self-excited aerodynamic flutter",
        ),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(
                id="Q0",
                question="Why did it twist?",
                why_viewer_cares="It matters",
                partial_answer="Flutter",
                claim_ids=["C001"],
                creates_next_question=None,
                information_gap="none left",
            )
        ],
        ti_role="Investigator",
        ending="Callback to the opening image",
        claim_ids_used=["C001"],
    )


def _valid_packaging_prototype() -> PackagingPrototype:
    return PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading="LOW",
    )


def _valid_script_plan(line_ids=("L001", "L002", "L003", "L004")) -> ScriptPlan:
    lines = [
        ScriptLine(line_id=lid, text=f"Đây là câu {lid}.", function="INFORM") for lid in line_ids
    ]
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=lines[:2]),
            ScriptBeat(beat_id="B002", narrative_node="Q0", narrative_function="REVEAL", lines=lines[2:]),
        ],
        qa_status="PASS",
    )


def _create_project_at_mvp_complete(engine, script_plan=None):
    idea = _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    approve_idea(engine, project_id)

    research_r0 = _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = _valid_feasibility_report()
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    decide_feasibility(engine, project_id, GateStatus.PASS)

    research_package = _valid_research_package(idea.central_question)
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)

    narrative_plan = _valid_narrative_plan(idea.central_question)
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, narrative_plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", narrative_plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    approve_narrative(engine, project_id)

    packaging = _valid_packaging_prototype()
    save_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, packaging)
    update_artifact_reference(engine, project_id, "packaging_prototype_id", packaging.id)
    _record_module_run_success(
        engine,
        project_id,
        "packaging_p0_engine",
        [str(idea.id), str(research_package.id), str(narrative_plan.id)],
    )
    approve_packaging_p0(engine, project_id)

    script_plan = script_plan or _valid_script_plan()
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan.id)
    update_project_state(engine, project_id, ProjectState.SCRIPT_VERIFICATION)

    verification_report = ScriptVerificationReport(status="PASS")
    save_artifact(engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, verification_report)
    _record_module_run_success(
        engine,
        project_id,
        "script_verification_engine",
        [str(research_package.id), str(narrative_plan.id), str(packaging.id), str(script_plan.id)],
    )
    accept_script_verification(engine, project_id)
    approve_final_script(engine, project_id)
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    return project_id, script_plan


def _chunk(chunk_id, line_ids, take_count=1, voice_state="NEUTRAL", pace="NORMAL", energy="MEDIUM", music_state="BED", sfx=None) -> VoiceChunk:
    return VoiceChunk(
        chunk_id=chunk_id,
        line_ids=list(line_ids),
        voice_state=voice_state,
        pace=pace,
        energy=energy,
        take_count=take_count,
        music_state=music_state,
        sfx_opportunity=sfx,
    )


def _save_voice_plan(engine, project_id, script_plan_id, chunks) -> VoicePlan:
    voice_plan = VoicePlan(script_plan_id=script_plan_id, chunks=chunks)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    return voice_plan


def _create_project_ready_for_rendering(engine, chunks_factory=None):
    """A project at MVP_COMPLETE with a two-chunk VoicePlan already stored:
    C001 (take_count=1, lines L001-L002) and C002 (take_count=2, lines
    L003-L004) -- three total takes, matching the "1+2" happy-path shape."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    chunks = (
        chunks_factory()
        if chunks_factory
        else [
            _chunk("C001", ["L001", "L002"], take_count=1),
            _chunk("C002", ["L003", "L004"], take_count=2, voice_state="EXCITED", pace="FAST", energy="HIGH"),
        ]
    )
    voice_plan = _save_voice_plan(engine, project_id, script_plan.id, chunks)
    return project_id, script_plan, voice_plan


def _tts_response(**overrides) -> TTSResponse:
    fields = dict(audio_bytes=b"RIFF-fake-audio-bytes", provider="fake-tts", audio_format="WAV")
    fields.update(overrides)
    return TTSResponse(**fields)


def _settings(**overrides) -> TTSSettings:
    fields = dict(provider="fake-tts", voice_id="voice-01")
    fields.update(overrides)
    return TTSSettings(**fields)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_renders_three_takes_for_one_plus_two_chunks(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider(
        [_tts_response(duration_seconds=1.0) for _ in range(3)]
    )
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    result = renderer.run(VoiceRendererInput(project_id=project_id))

    assert provider.call_count == 3
    assert result.provider_call_count == 3
    assert result.rendered_take_count == 3
    assert len(result.manifest.renders) == 3
    assert result.total_duration_seconds == pytest.approx(3.0)

    for render in result.manifest.renders:
        written_path = store.root / render.file_path
        assert written_path.exists()
        assert written_path.read_bytes() == b"RIFF-fake-audio-bytes"

    stored = get_artifact(engine, project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest)
    assert stored == result.manifest

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "voice_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.SUCCESS
    assert render_runs[0].output_id == str(result.manifest.id)
    assert render_runs[0].input_ids == [str(script_plan.id), str(voice_plan.id)]


# ---------------------------------------------------------------------------
# Exact text ownership
# ---------------------------------------------------------------------------


def test_chunk_text_is_verbatim_newline_join_of_script_line_text(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider([_tts_response() for _ in range(3)])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    renderer.run(VoiceRendererInput(project_id=project_id))

    line_text = {line.line_id: line.text for beat in script_plan.beats for line in beat.lines}
    expected_c001_text = "\n".join([line_text["L001"], line_text["L002"]])
    expected_c002_text = "\n".join([line_text["L003"], line_text["L004"]])

    assert provider.received_requests[0].text == expected_c001_text
    assert provider.received_requests[1].text == expected_c002_text
    assert provider.received_requests[2].text == expected_c002_text


# ---------------------------------------------------------------------------
# Delivery-metadata passthrough
# ---------------------------------------------------------------------------


def test_delivery_metadata_passed_through_unchanged(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider([_tts_response() for _ in range(3)])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    renderer.run(VoiceRendererInput(project_id=project_id))

    c001_request = provider.received_requests[0]
    assert c001_request.voice_state.value == "NEUTRAL"
    assert c001_request.pace.value == "NORMAL"
    assert c001_request.energy.value == "MEDIUM"

    c002_request = provider.received_requests[1]
    assert c002_request.voice_state.value == "EXCITED"
    assert c002_request.pace.value == "FAST"
    assert c002_request.energy.value == "HIGH"

    for request in provider.received_requests:
        assert request.voice_id == "voice-01"
        assert request.output_format.value == "WAV"


# ---------------------------------------------------------------------------
# Take-count -> job-count/id correctness, and call order
# ---------------------------------------------------------------------------


def test_take_count_determines_job_count_and_ids_in_chunk_then_take_order(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider([_tts_response() for _ in range(3)])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    result = renderer.run(VoiceRendererInput(project_id=project_id))

    job_ids_in_call_order = [req.metadata["render_job_id"] for req in provider.received_requests]
    assert job_ids_in_call_order == ["C001_T1", "C002_T1", "C002_T2"]

    manifest_job_ids = [render.render_job_id for render in result.manifest.renders]
    assert manifest_job_ids == ["C001_T1", "C002_T1", "C002_T2"]


# ---------------------------------------------------------------------------
# Stale VoicePlan gate
# ---------------------------------------------------------------------------


def test_stale_voice_plan_blocks_before_any_side_effect(engine, tmp_path):
    project_id, script_plan_a, voice_plan = _create_project_ready_for_rendering(engine)

    # The script changes underneath the stored VoicePlan.
    script_plan_b = _valid_script_plan(("L001", "L002", "L003", "L004"))
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan_b)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan_b.id)

    provider = FakeTTSProvider([])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    with pytest.raises(StaleVoicePlanError):
        renderer.run(VoiceRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert list((tmp_path / "audio").glob("**/*")) == []
    assert not any(
        run.module == "voice_renderer" for run in list_module_runs_for_project(engine, project_id)
    )
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest)


# ---------------------------------------------------------------------------
# Provider retry
# ---------------------------------------------------------------------------


def test_provider_retry_succeeds_within_bound(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _save_voice_plan(
        engine, project_id, script_plan.id, [_chunk("C001", ["L001", "L002"], take_count=1)]
    )
    provider = FakeTTSProvider([TTSProviderError("transient upstream error"), _tts_response()])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(max_provider_retries=1), store)

    result = renderer.run(VoiceRendererInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.provider_call_count == 2
    assert result.rendered_take_count == 1


def test_provider_retry_exhaustion_propagates_and_marks_module_run_failed(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    _save_voice_plan(
        engine, project_id, script_plan.id, [_chunk("C001", ["L001", "L002"], take_count=1)]
    )
    provider = FakeTTSProvider(
        [TTSProviderError("down"), TTSProviderError("still down")]
    )
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(max_provider_retries=1), store)

    with pytest.raises(TTSProviderError):
        renderer.run(VoiceRendererInput(project_id=project_id))

    assert provider.call_count == 2

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "voice_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest)


# ---------------------------------------------------------------------------
# No retry on a file-write failure
# ---------------------------------------------------------------------------


def test_write_failure_is_not_retried_against_the_provider(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    _save_voice_plan(
        engine, project_id, script_plan.id, [_chunk("C001", ["L001", "L002"], take_count=1)]
    )
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    # A directory already sits where the renderer's only output file must go,
    # so AudioFileStore.write's final rename fails.
    (audio_root / "C001_T1.wav").mkdir()

    # Only ONE response queued: if the renderer wrongly retried the provider
    # after the write failure, FakeTTSProvider would raise its own
    # "exhausted" TTSProviderError instead -- a different, wrong exception.
    provider = FakeTTSProvider([_tts_response()])
    store = AudioFileStore(audio_root)
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    with pytest.raises(AudioWriteError):
        renderer.run(VoiceRendererInput(project_id=project_id))

    assert provider.call_count == 1

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "voice_renderer"]
    assert render_runs[0].status == ModuleRunStatus.FAILED


def test_partial_write_failure_leaves_earlier_files_orphaned_and_no_manifest(engine, tmp_path):
    """Documents actual behavior: a mid-run failure does not roll back
    already-written files -- only the manifest artifact is withheld."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    _save_voice_plan(
        engine,
        project_id,
        script_plan.id,
        [_chunk("C001", ["L001", "L002"]), _chunk("C002", ["L003", "L004"])],
    )
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    (audio_root / "C002_T1.wav").mkdir()  # only the second chunk's write fails

    provider = FakeTTSProvider([_tts_response(), _tts_response()])
    store = AudioFileStore(audio_root)
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    with pytest.raises(AudioWriteError):
        renderer.run(VoiceRendererInput(project_id=project_id))

    assert (audio_root / "C001_T1.wav").exists()  # orphaned, not cleaned up
    assert (audio_root / "C001_T1.wav").read_bytes() == b"RIFF-fake-audio-bytes"

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest)


# ---------------------------------------------------------------------------
# Manifest integrity wiring (defense in depth)
# ---------------------------------------------------------------------------


def test_manifest_integrity_failure_blocks_persistence(engine, tmp_path, monkeypatch):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider([_tts_response() for _ in range(3)])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    import app.renderers.voice.renderer as renderer_module

    monkeypatch.setattr(
        renderer_module, "validate_voice_render_manifest", lambda *args, **kwargs: ["synthetic issue"]
    )

    with pytest.raises(VoiceRenderManifestIntegrityError):
        renderer.run(VoiceRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "voice_renderer"]
    assert render_runs[0].status == ModuleRunStatus.FAILED
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest)


# ---------------------------------------------------------------------------
# Manifest persistence/retrieval and rerender/upsert semantics
# ---------------------------------------------------------------------------


def test_rerender_overwrites_the_stored_manifest(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider([_tts_response() for _ in range(6)])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    first_result = renderer.run(VoiceRendererInput(project_id=project_id))
    second_result = renderer.run(VoiceRendererInput(project_id=project_id))

    assert first_result.manifest.id != second_result.manifest.id
    stored = get_artifact(engine, project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest)
    assert stored == second_result.manifest

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "voice_renderer"]
    assert len(render_runs) == 2
    assert all(run.status == ModuleRunStatus.SUCCESS for run in render_runs)


# ---------------------------------------------------------------------------
# ScriptPlan / VoicePlan immutability
# ---------------------------------------------------------------------------


def test_rendering_never_mutates_script_plan_or_voice_plan(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    provider = FakeTTSProvider([_tts_response() for _ in range(3)])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    renderer.run(VoiceRendererInput(project_id=project_id))

    assert get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan) == script_plan
    assert get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan) == voice_plan


# ---------------------------------------------------------------------------
# Duration aggregation
# ---------------------------------------------------------------------------


def test_total_duration_is_sum_when_all_takes_report_duration(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    _save_voice_plan(
        engine,
        project_id,
        script_plan.id,
        [_chunk("C001", ["L001", "L002"]), _chunk("C002", ["L003", "L004"])],
    )
    provider = FakeTTSProvider(
        [_tts_response(duration_seconds=2.5), _tts_response(duration_seconds=1.5)]
    )
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    result = renderer.run(VoiceRendererInput(project_id=project_id))
    assert result.total_duration_seconds == pytest.approx(4.0)


def test_total_duration_is_none_when_any_take_duration_is_unknown(engine, tmp_path):
    """Do not guess: a single missing duration makes the total unknown."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    _save_voice_plan(
        engine,
        project_id,
        script_plan.id,
        [_chunk("C001", ["L001", "L002"]), _chunk("C002", ["L003", "L004"])],
    )
    provider = FakeTTSProvider(
        [_tts_response(duration_seconds=2.5), _tts_response(duration_seconds=None)]
    )
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    result = renderer.run(VoiceRendererInput(project_id=project_id))
    assert result.total_duration_seconds is None


# ---------------------------------------------------------------------------
# Invalid start state / missing artifacts
# ---------------------------------------------------------------------------


def test_invalid_start_state_rejected(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.state = ProjectState.SCRIPT_REVIEW.value

    provider = FakeTTSProvider([])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    with pytest.raises(RendererStateError):
        renderer.run(VoiceRendererInput(project_id=project_id))
    assert provider.call_count == 0


def test_missing_script_plan_artifact(engine, tmp_path):
    project_id, script_plan, voice_plan = _create_project_ready_for_rendering(engine)
    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.script_plan_id = None

    provider = FakeTTSProvider([])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingScriptPlanArtifactError):
        renderer.run(VoiceRendererInput(project_id=project_id))
    assert provider.call_count == 0


def test_missing_voice_plan_artifact(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    # No VoicePlan artifact saved at all.
    provider = FakeTTSProvider([])
    store = AudioFileStore(tmp_path / "audio")
    renderer = VoiceRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingVoicePlanArtifactError):
        renderer.run(VoiceRendererInput(project_id=project_id))
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# LLM-absence audit
# ---------------------------------------------------------------------------


_LLM_IMPORT_RE = re.compile(r"^\s*(import\s+app\.llm\b|from\s+app\.llm\b)")


def test_voice_renderer_source_has_no_llm_import_statement():
    renderers_root = Path(__file__).resolve().parent.parent / "app" / "renderers"
    offending = []
    for path in sorted(renderers_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _LLM_IMPORT_RE.match(line):
                offending.append(f"{path}:{lineno}: {line.strip()}")
    assert offending == []


def test_importing_voice_renderer_never_loads_app_llm():
    """Runs in a fresh subprocess -- within this test process, an earlier
    test module may have already imported app.llm, which would make an
    in-process sys.modules check meaningless."""
    project_root = Path(__file__).resolve().parent.parent
    script = (
        "import sys\n"
        "import app.renderers.voice\n"
        "loaded = [name for name in sys.modules "
        "if name == 'app.llm' or name.startswith('app.llm.')]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
