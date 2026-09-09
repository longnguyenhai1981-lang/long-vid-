"""Phase 34 requirements #44-46: the complete no-network production
pipeline (IDEA through FINAL_MEDIA_APPROVAL) driven end to end through
ProductionRunner only -- FakeLLMProvider/FakeResearchRetriever stand in
for the twelve upstream creative engines' own network calls,
FakeTTSProvider/FakeVisualProvider stand in for the seven Phase 33
downstream renderers' own network calls, and every human decision is a
REAL app/review/service.py call (approve_idea, decide_feasibility,
approve_narrative, approve_packaging_p0, accept_script_verification,
approve_final_script) or app/orchestration/gates.py's approve_gate
(FINAL_MEDIA_APPROVAL only). No artifact is pre-seeded except the
initial production input (an IdeaEngineInput) -- everything else is
produced by the runner calling real adapters/engines.

VOICE_PLAN/VISUAL_PLAN/ASSEMBLY_PLAN/PACKAGING_P1's own structured
output schemas carry REAL foreign-key fields the fake LLM provider must
echo verbatim (script_plan_id/voice_plan_id/visual_plan_id/
assembly_plan_id/packaging_prototype_id are never overridden by those
four engines, unlike idea_id/central_question elsewhere in the chain --
confirmed by reading each engine.py directly) -- so those four fixtures
can only be constructed AFTER the real upstream id they reference has
actually been generated. This is why the trace below is more than five
runs: every stage whose own JSON must reference a not-yet-known real id
gets its own run_until() call, injecting a freshly-built fixture between
calls, exactly as a real caller preparing real LLM prompts would have to
"know" the current artifact id before generating the next one.
"""

from __future__ import annotations

import io
import math
import shutil
import struct
import wave
from datetime import datetime, timezone
from uuid import UUID

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH -- skipping full no-network production integration test",
)

from app.audio.config import TTSSettings
from app.audio.fake import FakeTTSProvider
from app.audio.models import TTSResponse
from app.audio.storage import AudioFileStore
from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import DiscoveryMode, IdeaEngineInput
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.media_qc.models import MediaQCReport
from app.models.assembly import AssemblyPlan
from app.models.common import GateStatus, ProjectState
from app.models.feasibility import FeasibilityReport
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.timeline import TimelineManifest
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.orchestration.gates import approve_gate
from app.orchestration.models import ApprovalGateType, ProductionNodeStatus, ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE
from app.research.fake import FakeResearchRetriever
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.artifacts import get_artifact
from app.storage.database import init_database
from app.storage.projects import create_project, update_project_state
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore
from tests.test_feasibility_engine import _feasibility_json
from tests.test_idea_engine import VALID_IDEA_JSON, _llm_settings
from tests.test_narrative_engine import _plan_json
from tests.test_packaging_p0_engine import _prototype_json
from tests.test_packaging_p1_engine import _final_packaging_plan_json
from tests.test_r0_research_engine import _evidence_response, _research_json
from tests.test_r1_research_engine import _package_json
from tests.test_script_engine import _beat_dict, _line_dict, _script_json
from tests.test_script_verification_engine import _report_json
from tests.test_visual_plan_engine import _visual_beat_dict, _visual_plan_json
from tests.test_voice_plan_engine import _voice_plan_json


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _real_png_bytes(color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _real_wav_bytes(duration_seconds: float, framerate: int = 8000, frequency: float = 440.0) -> bytes:
    buffer = io.BytesIO()
    frame_count = round(duration_seconds * framerate)
    samples = [int(16000 * math.sin(2 * math.pi * frequency * (i / framerate))) for i in range(frame_count)]
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def _new_bare_project(engine) -> UUID:
    """NEW_PROJECT -> IDEA_DISCOVERY is a trivial, review-free bootstrap
    transition every existing engine test performs as pure setup (no
    app/review/service.py function exists for it -- it is not a human
    decision, just "start production"), reproduced identically here."""
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Phase 34 full pipeline integration", created_at=created,
        updated_at=created, state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    update_project_state(engine, project.project_id, ProjectState.IDEA_DISCOVERY)
    return project.project_id


def test_full_pipeline_stops_at_every_real_gate_and_resumes_to_succeeded(tmp_path):
    engine = init_database(tmp_path / "db.sqlite")
    project_id = _new_bare_project(engine)

    graph = build_full_graph()
    adapters = build_full_adapters()
    runner = ProductionRunner(engine, graph, adapters)

    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    ctx = ExecutionContext(
        db_engine=engine,
        project_id=project_id,
        llm_settings=_llm_settings(),
        research_retriever=FakeResearchRetriever([evidence] * 30),
        initial_idea_input=IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN),
    )

    # --- RUN 1: IDEA -----------------------------------------------------
    ctx.llm_provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    run1 = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx)
    assert run1.status is ProductionRunStatus.WAITING_APPROVAL
    assert run1.waiting_gate == ApprovalGateType.IDEA_APPROVAL.value
    assert run1.node_states["IDEA"].executed_this_run is True
    approve_idea(engine, project_id)

    # --- RUN 2: RESEARCH_R0 -> FEASIBILITY --------------------------------
    ctx.llm_provider = FakeLLMProvider([_response(_research_json(project_id)), _response(_feasibility_json())])
    run2 = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run1.id)
    assert run2.status is ProductionRunStatus.WAITING_APPROVAL
    assert run2.waiting_gate == ApprovalGateType.RESEARCH_APPROVAL.value
    assert run2.node_states["IDEA"].reused_existing_artifact is True
    assert run2.node_states["RESEARCH_R0"].executed_this_run is True
    assert run2.node_states["FEASIBILITY"].executed_this_run is True
    decide_feasibility(engine, project_id, GateStatus.PASS)

    # --- RUN 3: RESEARCH_R1 -> NARRATIVE -----------------------------------
    ctx.llm_provider = FakeLLMProvider([_response(_package_json()), _response(_plan_json())])
    run3 = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run1.id)
    assert run3.status is ProductionRunStatus.WAITING_APPROVAL
    assert run3.waiting_gate == ApprovalGateType.NARRATIVE_APPROVAL.value
    approve_narrative(engine, project_id)

    # --- RUN 4: PACKAGING_P0 ----------------------------------------------
    ctx.llm_provider = FakeLLMProvider([_response(_prototype_json())])
    run4 = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run1.id)
    assert run4.status is ProductionRunStatus.WAITING_APPROVAL
    assert run4.waiting_gate == ApprovalGateType.PACKAGING_P0_APPROVAL.value
    approve_packaging_p0(engine, project_id)

    # --- RUN 5: SCRIPT -> SCRIPT_VERIFY -------------------------------------
    # Four lines across two beats -- matching what VOICE_PLAN/VISUAL_PLAN's
    # own default fixtures (C001/C002 chunks, VB001/VB002 beats) expect to
    # cover later; _script_json()'s own bare default is a single L001 line.
    four_line_beats = [
        _beat_dict("B001", lines=[_line_dict("L001"), _line_dict("L002")]),
        _beat_dict("B002", lines=[_line_dict("L003"), _line_dict("L004")]),
    ]
    ctx.llm_provider = FakeLLMProvider(
        [_response(_script_json(beats=four_line_beats)), _response(_report_json())]
    )
    run5 = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run1.id)
    assert run5.status is ProductionRunStatus.WAITING_APPROVAL
    assert run5.waiting_gate == ApprovalGateType.SCRIPT_APPROVAL.value
    accept_script_verification(engine, project_id)
    approve_final_script(engine, project_id)

    # --- RUN 6: the whole upstream creative chain reaches SUCCEEDED --------
    run6 = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run1.id)
    assert run6.status is ProductionRunStatus.SUCCEEDED
    for node_id in (
        "IDEA", "RESEARCH_R0", "FEASIBILITY", "RESEARCH_R1", "NARRATIVE",
        "PACKAGING_P0", "SCRIPT", "SCRIPT_VERIFY",
    ):
        assert run6.node_states[node_id].status is ProductionNodeStatus.SUCCEEDED

    # Idempotency: calling again with nothing changed reuses everything.
    run6b = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run1.id)
    assert all(record.reused_existing_artifact for record in run6b.node_states.values())

    # --- RUN 7: VOICE_PLAN (needs the REAL script_plan.id) ------------------
    script_plan = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    ctx.llm_provider = FakeLLMProvider([_response(_voice_plan_json(script_plan.id)) for _ in range(5)])
    run7 = runner.run_until(project_id, "VOICE_PLAN", ctx=ctx, run_id=run1.id)
    assert run7.node_states["VOICE_PLAN"].status is ProductionNodeStatus.SUCCEEDED

    # --- RUN 8: VISUAL_PLAN (needs the REAL voice_plan.id) ------------------
    voice_plan = get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
    assert voice_plan.script_plan_id == script_plan.id
    beats = [
        _visual_beat_dict("VB001", ["L001", "L002"], media_type="GENERATED_STILL"),
        _visual_beat_dict("VB002", ["L003", "L004"], media_type="GENERATED_STILL"),
    ]
    ctx.llm_provider = FakeLLMProvider(
        [_response(_visual_plan_json(script_plan.id, voice_plan.id, beats=beats)) for _ in range(5)]
    )
    run8 = runner.run_until(project_id, "VISUAL_PLAN", ctx=ctx, run_id=run1.id)
    assert run8.node_states["VISUAL_PLAN"].status is ProductionNodeStatus.SUCCEEDED

    # --- RUN 9: ASSEMBLY_PLAN (needs REAL voice/visual plan ids) ------------
    visual_plan = get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
    assert visual_plan.script_plan_id == script_plan.id
    assert visual_plan.voice_plan_id == voice_plan.id
    from tests.test_assembly_plan_engine import _assembly_plan_json

    ctx.llm_provider = FakeLLMProvider(
        [_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id)) for _ in range(5)]
    )
    run9 = runner.run_until(project_id, "ASSEMBLY_PLAN", ctx=ctx, run_id=run1.id)
    assert run9.node_states["ASSEMBLY_PLAN"].status is ProductionNodeStatus.SUCCEEDED

    # --- RUN 10: PACKAGING_P1 (needs REAL packaging/script/visual/assembly ids)
    assembly_plan = get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
    assert assembly_plan.visual_plan_id == visual_plan.id
    packaging = get_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
    ctx.llm_provider = FakeLLMProvider(
        [_response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id)) for _ in range(5)]
    )
    run10 = runner.run_until(project_id, "PACKAGING_P1", ctx=ctx, run_id=run1.id)
    assert run10.node_states["PACKAGING_P1"].status is ProductionNodeStatus.SUCCEEDED
    final_packaging = get_artifact(engine, project_id, "packaging_p1", FinalPackagingPlan)
    assert final_packaging.assembly_plan_id == assembly_plan.id

    # --- RUN 11: the downstream renderer slice through MEDIA_QC -------------
    ctx.audio_store = AudioFileStore(tmp_path / "audio")
    ctx.visual_store = VisualFileStore(tmp_path / "visuals")
    ctx.video_store = VideoFileStore(tmp_path / "video")
    ctx.subtitle_store = SubtitleFileStore(tmp_path / "subtitles")
    ctx.tts_provider = FakeTTSProvider(
        [TTSResponse(audio_bytes=_real_wav_bytes(2.0), provider="fake-tts", audio_format="WAV") for _ in range(10)]
    )
    ctx.tts_settings = TTSSettings(provider="fake-tts", voice_id="voice-01")
    ctx.visual_provider = FakeVisualProvider(
        [
            VisualRenderResponse(
                asset_bytes=_real_png_bytes((200, 30, 30) if i % 2 == 0 else (30, 30, 200)),
                provider="fake-visual", output_format="PNG",
            )
            for i in range(10)
        ]
    )
    ctx.visual_settings = VisualSettings(provider="fake-visual")
    ctx.burn_in_settings = CaptionRenderSettings()

    run11 = runner.run_until(project_id, "MEDIA_QC", ctx=ctx, run_id=run1.id)
    assert run11.status is ProductionRunStatus.WAITING_APPROVAL
    assert run11.waiting_gate == ApprovalGateType.FINAL_MEDIA_APPROVAL.value
    for node_id in (
        "VOICE_RENDER", "VISUAL_RENDER", "TIMELINE", "VIDEO_RENDER", "CAPTION_BUILD", "SUBTITLE_EXPORT",
    ):
        assert run11.node_states[node_id].status is ProductionNodeStatus.SUCCEEDED

    qc_report_id = UUID(run11.node_states["MEDIA_QC"].artifact_id)
    qc_report = get_artifact(engine, project_id, MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCReport)
    assert qc_report.id == qc_report_id
    timeline_manifest = get_artifact(engine, project_id, TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineManifest)
    assert qc_report.timeline_manifest_id == timeline_manifest.id
    assert timeline_manifest.assembly_plan_id == assembly_plan.id

    # --- RUN 12: approve the final media, resume to SUCCEEDED ---------------
    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, qc_report_id)
    run12 = runner.resume(run1.id, ctx=ctx)
    assert run12.status is ProductionRunStatus.SUCCEEDED
    assert run12.id == run1.id
    # PACKAGING_P1 is a dead-end branch off ASSEMBLY_PLAN that nothing else
    # depends on -- MEDIA_QC's own ancestors_closure() never includes it,
    # exactly like Phase 33's own target-never-executes-beyond-itself rule.
    assert set(run12.node_states.keys()) == set(graph.node_ids()) - {"PACKAGING_P1"}
    for record in run12.node_states.values():
        assert record.status is ProductionNodeStatus.SUCCEEDED
        assert record.reused_existing_artifact is True
        assert record.executed_this_run is False
