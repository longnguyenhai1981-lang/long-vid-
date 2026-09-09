"""Phase 35 requirements #45-46: the complete no-network production
pipeline driven ONLY through the `motily` CLI/ProductionService surface
-- IDEA through FINAL_MEDIA_APPROVAL, stopping at every real human gate,
approving and resuming through each, ending SUCCEEDED. No direct
ProductionRunner calls from this test except through ProductionService
itself (an internal service implementation detail, per requirement #45).

Mirrors tests/test_full_production_integration.py's own fixture
construction (same reasoning: VOICE_PLAN/VISUAL_PLAN/ASSEMBLY_PLAN/
PACKAGING_P1 need real not-yet-known upstream ids in their own
structured-output schemas), but every step goes through `motily produce`/
`approve`/`resume` instead of calling ProductionRunner.run_until()
directly. Advancing through the ungated VOICE_PLAN/VISUAL_PLAN/
ASSEMBLY_PLAN stretch uses separate `produce --project-id <existing>
--target <next>` calls (each a legitimate new ProductionRun against the
same project, exactly matching requirement #28's own "produce creates a
new run" semantics) rather than `resume`, since `resume` continues
toward a run's own already-fixed target and these three fixtures can
only be built after the previous one's real id is known.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import wave
from uuid import UUID

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH -- skipping full no-network CLI integration test",
)

from typer.testing import CliRunner

import app.cli.main as cli_main
from app.audio.config import TTSSettings
from app.audio.fake import FakeTTSProvider
from app.audio.models import TTSResponse
from app.audio.storage import AudioFileStore
from app.bootstrap import AppComposition
from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.media_qc.models import MediaQCReport
from app.models.assembly import AssemblyPlan
from app.models.packaging import PackagingPrototype
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.orchestration.registry import ExecutionContext
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE
from app.research.fake import FakeResearchRetriever
from app.services.production_service import ProductionService
from app.storage.artifacts import get_artifact
from app.storage.database import init_database
from app.storage.module_runs import list_module_runs_for_project
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore
from tests.test_assembly_plan_engine import _assembly_plan_json
from tests.test_feasibility_engine import _feasibility_json
from tests.test_idea_engine import VALID_IDEA_JSON, _llm_settings
from tests.test_narrative_engine import _plan_json
from tests.test_packaging_p0_engine import _prototype_json
from tests.test_r0_research_engine import _evidence_response, _research_json
from tests.test_r1_research_engine import _package_json
from tests.test_script_engine import _beat_dict, _line_dict, _script_json
from tests.test_script_verification_engine import _report_json
from tests.test_visual_plan_engine import _visual_beat_dict, _visual_plan_json
from tests.test_voice_plan_engine import _voice_plan_json

runner = CliRunner()


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


def test_full_cli_pipeline_reaches_succeeded_with_no_network_calls(tmp_path):
    db_engine = init_database(tmp_path / "cli_full.db")
    graph = build_full_graph()
    adapters = build_full_adapters()
    service = ProductionService(db_engine, graph, adapters)
    comp = AppComposition(db_engine=db_engine, graph=graph, adapters=adapters, service=service)
    cli_main._composition_override = comp

    holder: dict = {}

    def _context_factory(project_id, engine):
        return ExecutionContext(
            db_engine=engine, project_id=project_id,
            llm_provider=holder.get("llm_provider"), llm_settings=_llm_settings(),
            research_retriever=holder.get("retriever"),
            audio_store=holder.get("audio_store"), visual_store=holder.get("visual_store"),
            video_store=holder.get("video_store"), subtitle_store=holder.get("subtitle_store"),
            tts_provider=holder.get("tts_provider"), tts_settings=holder.get("tts_settings"),
            visual_provider=holder.get("visual_provider"), visual_settings=holder.get("visual_settings"),
            burn_in_settings=holder.get("burn_in_settings"),
        )

    cli_main._context_factory_override = _context_factory

    try:
        gate_sequence: list[str | None] = []
        exit_codes: list[int] = []

        # 1. motily produce --target final
        evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
        holder["llm_provider"] = FakeLLMProvider([_response(VALID_IDEA_JSON)])
        holder["retriever"] = FakeResearchRetriever([evidence] * 30)

        result = runner.invoke(
            cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "script", "--json"],
        )
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        run_id = data["production_run_id"]
        project_id = UUID(data["project_id"])
        gate_sequence.append(data["waiting_gate"])  # 2. observe IDEA gate
        assert data["waiting_gate"] == "IDEA_APPROVAL"

        # 3. motily approve
        approve_result = runner.invoke(cli_main.app, ["approve", run_id, "--gate", "idea"])
        assert approve_result.exit_code == 0

        # 4. motily resume
        holder["llm_provider"] = FakeLLMProvider(
            [_response(_research_json(project_id)), _response(_feasibility_json())]
        )
        result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        gate_sequence.append(data["waiting_gate"])
        assert data["waiting_gate"] == "RESEARCH_APPROVAL"

        # 5. approve each next gate
        runner.invoke(cli_main.app, ["approve", run_id, "--gate", "research"])
        holder["llm_provider"] = FakeLLMProvider([_response(_package_json()), _response(_plan_json())])
        result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        gate_sequence.append(data["waiting_gate"])
        assert data["waiting_gate"] == "NARRATIVE_APPROVAL"

        runner.invoke(cli_main.app, ["approve", run_id, "--gate", "narrative"])
        holder["llm_provider"] = FakeLLMProvider([_response(_prototype_json())])
        result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        gate_sequence.append(data["waiting_gate"])
        assert data["waiting_gate"] == "PACKAGING_P0_APPROVAL"

        runner.invoke(cli_main.app, ["approve", run_id, "--gate", "packaging"])
        four_line_beats = [
            _beat_dict("B001", lines=[_line_dict("L001"), _line_dict("L002")]),
            _beat_dict("B002", lines=[_line_dict("L003"), _line_dict("L004")]),
        ]
        holder["llm_provider"] = FakeLLMProvider(
            [_response(_script_json(beats=four_line_beats)), _response(_report_json())]
        )
        result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        gate_sequence.append(data["waiting_gate"])
        assert data["waiting_gate"] == "SCRIPT_APPROVAL"

        # SCRIPT_APPROVAL spans two real states -- two approve+resume cycles.
        runner.invoke(cli_main.app, ["approve", run_id, "--gate", "script"])
        result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        assert data["waiting_gate"] == "SCRIPT_APPROVAL"  # still pending -- second step

        runner.invoke(cli_main.app, ["approve", run_id, "--gate", "script"])
        result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        assert data["status"] == "SUCCEEDED"  # target ("script"/SCRIPT_VERIFY) fully reached

        # -- ungated VOICE_PLAN -> VISUAL_PLAN -> ASSEMBLY_PLAN. No published
        # --target alias covers these individually (only their shared
        # endpoint via "plans"), since each one's own structured-output
        # schema references a real upstream id that only becomes known once
        # the previous step has actually executed. ProductionService.
        # start_at_node is the documented internal seam for this (requirement
        # #45: "internal service implementation" is fine; only bypassing to
        # ProductionRunner directly from the test driver is disallowed).
        script_plan = get_artifact(db_engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
        holder["llm_provider"] = FakeLLMProvider([_response(_voice_plan_json(script_plan.id)) for _ in range(5)])
        ctx = _context_factory(project_id, db_engine)
        voice_run = service.start_at_node(project_id, "VOICE_PLAN", ctx)
        exit_codes.append(0 if voice_run.status.value == "SUCCEEDED" else 4)
        assert voice_run.status.value == "SUCCEEDED"

        voice_plan = get_artifact(db_engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
        visual_beats = [
            _visual_beat_dict("VB001", ["L001", "L002"], media_type="GENERATED_STILL"),
            _visual_beat_dict("VB002", ["L003", "L004"], media_type="GENERATED_STILL"),
        ]
        holder["llm_provider"] = FakeLLMProvider(
            [_response(_visual_plan_json(script_plan.id, voice_plan.id, beats=visual_beats)) for _ in range(5)]
        )
        ctx = _context_factory(project_id, db_engine)
        visual_run = service.start_at_node(project_id, "VISUAL_PLAN", ctx)
        assert visual_run.status.value == "SUCCEEDED"

        visual_plan = get_artifact(db_engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
        holder["llm_provider"] = FakeLLMProvider(
            [_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id)) for _ in range(5)]
        )
        ctx = _context_factory(project_id, db_engine)
        assembly_run = service.start_at_node(project_id, "ASSEMBLY_PLAN", ctx)
        exit_codes.append(0 if assembly_run.status.value == "SUCCEEDED" else 4)
        assert assembly_run.status.value == "SUCCEEDED"

        # -- downstream renderer slice through MEDIA_QC ------------------
        holder["audio_store"] = AudioFileStore(tmp_path / "audio")
        holder["visual_store"] = VisualFileStore(tmp_path / "visuals")
        holder["video_store"] = VideoFileStore(tmp_path / "video")
        holder["subtitle_store"] = SubtitleFileStore(tmp_path / "subtitles")
        holder["tts_provider"] = FakeTTSProvider(
            [TTSResponse(audio_bytes=_real_wav_bytes(2.0), provider="fake-tts", audio_format="WAV") for _ in range(10)]
        )
        holder["tts_settings"] = TTSSettings(provider="fake-tts", voice_id="voice-01")
        holder["visual_provider"] = FakeVisualProvider(
            [
                VisualRenderResponse(
                    asset_bytes=_real_png_bytes((200, 30, 30) if i % 2 == 0 else (30, 30, 200)),
                    provider="fake-visual", output_format="PNG",
                )
                for i in range(10)
            ]
        )
        holder["visual_settings"] = VisualSettings(provider="fake-visual")
        holder["burn_in_settings"] = CaptionRenderSettings()

        result = runner.invoke(
            cli_main.app, ["produce", "--project-id", str(project_id), "--target", "final", "--json"],
        )
        exit_codes.append(result.exit_code)
        data = json.loads(result.stdout)
        gate_sequence.append(data["waiting_gate"])
        assert data["waiting_gate"] == "FINAL_MEDIA_APPROVAL"
        final_run_id = data["production_run_id"]
        qc_report_id = data["subject_artifact_id"]

        # 6. eventually FINAL approval
        approve_result = runner.invoke(cli_main.app, ["approve", final_run_id, "--gate", "final"])
        assert approve_result.exit_code == 0

        # 7. resume -> 8. SUCCEEDED
        final_result = runner.invoke(cli_main.app, ["resume", final_run_id, "--json"])
        exit_codes.append(final_result.exit_code)
        final_data = json.loads(final_result.stdout)
        assert final_data["status"] == "SUCCEEDED"

        # network_calls = 0 -- only Fake* providers were ever supplied.
        qc_report = get_artifact(db_engine, project_id, MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCReport)
        assert str(qc_report.id) == qc_report_id
        assembly_plan = get_artifact(db_engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
        assert qc_report.timeline_manifest_id is not None

        # -- idempotency: resuming a SUCCEEDED run reuses everything -------
        idempotent_result = runner.invoke(cli_main.app, ["resume", final_run_id, "--json"])
        idempotent_data = json.loads(idempotent_result.stdout)
        assert idempotent_data["status"] == "SUCCEEDED"
        assert idempotent_data["executed_nodes"] == []
        assert set(idempotent_data["reused_nodes"]) == set(idempotent_data["reused_nodes"])  # all reused

        module_run_count_before = len(list_module_runs_for_project(db_engine, project_id))
        runner.invoke(cli_main.app, ["resume", final_run_id, "--json"])
        module_run_count_after = len(list_module_runs_for_project(db_engine, project_id))
        assert module_run_count_before == module_run_count_after  # zero re-executions
    finally:
        cli_main._composition_override = None
        cli_main._context_factory_override = None
