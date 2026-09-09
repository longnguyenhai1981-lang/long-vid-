"""Phase 33 requirement #40: the real wired 7-node slice
(VOICE_RENDER -> VISUAL_RENDER -> TIMELINE -> VIDEO_RENDER ->
CAPTION_BUILD -> SUBTITLE_EXPORT -> MEDIA_QC) driven end to end through
ProductionRunner against REAL renderers/builders -- FakeTTSProvider and
FakeVisualProvider stand in for network TTS/image providers (never a
live API call), but every other step (ffmpeg encode, ffprobe QC
inspection, SRT export, caption burn-in) is the real local implementation.

Skipped cleanly when ffmpeg/ffprobe are not on PATH, exactly like
tests/test_media_qc_integration.py.
"""

from __future__ import annotations

import io
import math
import shutil
import struct
import wave
from uuid import UUID

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH -- skipping real orchestration integration test",
)

from app.audio.fake import FakeTTSProvider
from app.audio.config import TTSSettings
from app.audio.models import TTSResponse
from app.audio.storage import AudioFileStore
from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.models.visual import VisualBeat, VisualPlan
from app.models.voice import VoiceChunk, VoicePlan
from app.orchestration.adapters import build_default_adapters, build_default_graph
from app.orchestration.gates import approve_gate
from app.orchestration.models import ApprovalGateType, ProductionNodeStatus, ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner
from app.storage.artifacts import save_artifact
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore
from tests.test_timeline_builder import _assembly_plan
from tests.test_visual_renderer import _create_project_at_mvp_complete


def _real_png_bytes(color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _real_wav_bytes(duration_seconds: float, framerate: int = 8000, frequency: float = 440.0) -> bytes:
    """An audible sine tone, not silence -- MediaQCInspector's own
    QC_AUDIO_SILENCE check fails a real render on all-zero samples."""
    buffer = io.BytesIO()
    frame_count = round(duration_seconds * framerate)
    samples = [
        int(16000 * math.sin(2 * math.pi * frequency * (i / framerate))) for i in range(frame_count)
    ]
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def _build_ready_project(engine):
    """A real MVP_COMPLETE project with ScriptPlan/VoicePlan/VisualPlan/
    AssemblyPlan already saved -- everything the wired slice's own
    upstream (unwired) creative chain would have produced, stopping
    exactly at the boundary this phase actually orchestrates. Both
    visual beats are GENERATED_STILL (no ASSET_REUSE/TI_STATE/DIAGRAM),
    so a plain FakeVisualProvider needs no ti_compositor/diagram_renderer
    to fully render them."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)

    voice_plan = VoicePlan(
        script_plan_id=script_plan.id,
        chunks=[
            VoiceChunk(
                chunk_id="C001", line_ids=["L001", "L002"], voice_state="NEUTRAL",
                pace="NORMAL", energy="MEDIUM", take_count=1, music_state="BED",
            ),
            VoiceChunk(
                chunk_id="C002", line_ids=["L003", "L004"], voice_state="CURIOUS",
                pace="NORMAL", energy="MEDIUM", take_count=1, music_state="DUCK",
            ),
        ],
    )
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)

    visual_plan = VisualPlan(
        script_plan_id=script_plan.id,
        voice_plan_id=voice_plan.id,
        beats=[
            VisualBeat(
                beat_id="V1", script_line_ids=["L001", "L002"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="GENERATED_STILL",
                complexity="C1", concept="c1", primary_focus="f1",
            ),
            VisualBeat(
                beat_id="V2", script_line_ids=["L003", "L004"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="GENERATED_STILL",
                complexity="C1", concept="c2", primary_focus="f2",
            ),
        ],
    )
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    assembly_plan = _assembly_plan(script_plan.id, voice_plan.id, visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    return project_id


def _build_ctx(engine, project_id, tmp_path) -> ExecutionContext:
    return ExecutionContext(
        db_engine=engine,
        project_id=project_id,
        audio_store=AudioFileStore(tmp_path / "audio"),
        visual_store=VisualFileStore(tmp_path / "visuals"),
        video_store=VideoFileStore(tmp_path / "video"),
        subtitle_store=SubtitleFileStore(tmp_path / "subtitles"),
        tts_provider=FakeTTSProvider(
            [
                TTSResponse(audio_bytes=_real_wav_bytes(2.0), provider="fake-tts", audio_format="WAV")
                for _ in range(10)
            ]
        ),
        tts_settings=TTSSettings(provider="fake-tts", voice_id="voice-01"),
        visual_provider=FakeVisualProvider(
            [
                VisualRenderResponse(
                    asset_bytes=_real_png_bytes((200, 30, 30) if i % 2 == 0 else (30, 30, 200)),
                    provider="fake-visual", output_format="PNG",
                )
                for i in range(10)
            ]
        ),
        visual_settings=VisualSettings(provider="fake-visual"),
        burn_in_settings=CaptionRenderSettings(),
    )


def test_wired_slice_stops_at_final_media_approval_then_succeeds_on_resume(engine, tmp_path):
    project_id = _build_ready_project(engine)
    ctx = _build_ctx(engine, project_id, tmp_path)

    graph = build_default_graph()
    adapters = build_default_adapters()
    runner = ProductionRunner(engine, graph, adapters)

    first = runner.run_until(project_id, "MEDIA_QC", ctx=ctx)

    executed_first = [
        node_id for node_id, record in first.node_states.items() if record.executed_this_run
    ]
    assert set(executed_first) == {
        "VOICE_RENDER", "VISUAL_RENDER", "TIMELINE", "VIDEO_RENDER", "CAPTION_BUILD", "SUBTITLE_EXPORT", "MEDIA_QC",
    }
    for node_id in executed_first:
        if node_id == "MEDIA_QC":
            continue  # gated -- its own module succeeded but the run stops at WAITING_APPROVAL
        assert first.node_states[node_id].status is ProductionNodeStatus.SUCCEEDED

    assert first.status is ProductionRunStatus.WAITING_APPROVAL
    assert first.waiting_gate == ApprovalGateType.FINAL_MEDIA_APPROVAL.value
    qc_record = first.node_states["MEDIA_QC"]
    final_media_artifact_id = UUID(qc_record.artifact_id)

    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, final_media_artifact_id)
    resumed = runner.resume(first.id, ctx=ctx)

    assert resumed.status is ProductionRunStatus.SUCCEEDED
    assert resumed.id == first.id
    for node_id in executed_first:
        record = resumed.node_states[node_id]
        assert record.reused_existing_artifact is True
        assert record.executed_this_run is False

    # Reusing the same context/adapters/graph a third time with nothing
    # changed reuses every node again -- no unnecessary re-execution.
    third = runner.run_until(project_id, "MEDIA_QC", ctx=ctx)
    assert third.status is ProductionRunStatus.SUCCEEDED
    for node_id in executed_first:
        assert third.node_states[node_id].reused_existing_artifact is True
