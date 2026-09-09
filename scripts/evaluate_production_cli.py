"""Manual human-evaluation utility for Phase 35: the `motily` CLI itself.

Drives the REAL Typer app (app.cli.main.app) through Typer's own
CliRunner -- the exact same command dispatch a real `motily produce ...`
invocation goes through -- never calling ProductionRunner directly, and
prints a transcript-style log of every command and its output, exactly
like a human operator would see at a terminal.

Reuses scripts/evaluate_full_production_orchestrator.py's own canned
LLM JSON fixtures (idea/research/feasibility/... builders) rather than
re-deriving them -- this script is the CLI-facing counterpart to that
Phase 34 evaluation, not a replacement for it.

No live network call anywhere (FakeLLMProvider/FakeResearchRetriever/
FakeTTSProvider/FakeVisualProvider throughout).

Usage:

    python scripts/evaluate_production_cli.py
"""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import sys
import wave
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_full_production_orchestrator import (  # noqa: E402
    _assembly_plan_json,
    _feasibility_json,
    _IDEA_JSON,
    _llm_settings,
    _narrative_json,
    _packaging_p0_json,
    _research_r0_json,
    _research_r1_json,
    _script_json,
    _script_verification_json,
    _visual_plan_json,
    _voice_plan_json,
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
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.media_qc.models import MediaQCReport
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.orchestration.registry import ExecutionContext
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE
from app.research.fake import FakeResearchRetriever
from app.research.models import ResearchQuery, ResearchSearchResponse, RetrievedSource
from app.services.production_service import ProductionService
from app.storage.artifacts import get_artifact
from app.storage.database import init_database
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore

EVAL_DIR = Path("data/production_cli_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
REPORT_FILENAME = "motily_phase35_report.json"

cli_runner = CliRunner()


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _evidence() -> ResearchSearchResponse:
    return ResearchSearchResponse(
        query=ResearchQuery(query="placeholder"),
        results=[RetrievedSource(title="Real Paper", url="https://real.example/source")],
    )


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


def _run(args: list[str]) -> tuple[dict, int]:
    print(f"$ motily {' '.join(args)}")
    result = cli_runner.invoke(cli_main.app, [*args, "--json"])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = {}
    if "status" in data:
        print(f"{data['status']}: {data.get('waiting_gate') or data.get('stop_reason') or ''}")
    else:
        print(result.stdout.strip())
    print(f"(exit code {result.exit_code})")
    print()
    return data, result.exit_code


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg/ffprobe not found on PATH -- cannot run this evaluation.", file=sys.stderr)
        return 1

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if EVAL_DB_PATH.is_file():
        EVAL_DB_PATH.unlink()
    db_engine = init_database(EVAL_DB_PATH)
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

    commands_run: list[str] = []
    gate_sequence: list[str] = []
    exit_codes: list[int] = []

    def track(args: list[str]) -> dict:
        commands_run.append("motily " + " ".join(args))
        data, exit_code = _run(args)
        exit_codes.append(exit_code)
        return data

    holder["llm_provider"] = FakeLLMProvider([_response(_IDEA_JSON)])
    holder["retriever"] = FakeResearchRetriever([_evidence() for _ in range(30)])

    data = track(["produce", "--brief", "Tacoma bridge", "--target", "script"])
    run_id = data["production_run_id"]
    project_id = UUID(data["project_id"])
    gate_sequence.append(data["waiting_gate"])

    track(["approve", run_id, "--gate", "idea"])

    holder["llm_provider"] = FakeLLMProvider([_response(_research_r0_json()), _response(_feasibility_json())])
    data = track(["resume", run_id])
    gate_sequence.append(data["waiting_gate"])
    track(["approve", run_id, "--gate", "research"])

    holder["llm_provider"] = FakeLLMProvider([_response(_research_r1_json()), _response(_narrative_json())])
    data = track(["resume", run_id])
    gate_sequence.append(data["waiting_gate"])
    track(["approve", run_id, "--gate", "narrative"])

    holder["llm_provider"] = FakeLLMProvider([_response(_packaging_p0_json())])
    data = track(["resume", run_id])
    gate_sequence.append(data["waiting_gate"])
    track(["approve", run_id, "--gate", "packaging"])

    holder["llm_provider"] = FakeLLMProvider([_response(_script_json()), _response(_script_verification_json())])
    data = track(["resume", run_id])
    gate_sequence.append(data["waiting_gate"])

    track(["approve", run_id, "--gate", "script"])
    data = track(["resume", run_id])  # SCRIPT_APPROVAL step 1 of 2 -- still pending
    track(["approve", run_id, "--gate", "script"])
    data = track(["resume", run_id])  # step 2 of 2 -- SCRIPT_VERIFY target reached

    script_plan = get_artifact(db_engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    holder["llm_provider"] = FakeLLMProvider([_response(_voice_plan_json(script_plan.id)) for _ in range(5)])
    ctx = _context_factory(project_id, db_engine)
    print("$ (internal) advance VOICE_PLAN")
    service.start_at_node(project_id, "VOICE_PLAN", ctx)
    print()

    voice_plan = get_artifact(db_engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
    holder["llm_provider"] = FakeLLMProvider(
        [_response(_visual_plan_json(script_plan.id, voice_plan.id)) for _ in range(5)]
    )
    ctx = _context_factory(project_id, db_engine)
    print("$ (internal) advance VISUAL_PLAN")
    service.start_at_node(project_id, "VISUAL_PLAN", ctx)
    print()

    visual_plan = get_artifact(db_engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
    holder["llm_provider"] = FakeLLMProvider(
        [_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id)) for _ in range(5)]
    )
    ctx = _context_factory(project_id, db_engine)
    print("$ (internal) advance ASSEMBLY_PLAN")
    service.start_at_node(project_id, "ASSEMBLY_PLAN", ctx)
    print()

    holder["audio_store"] = AudioFileStore(EVAL_DIR / "audio")
    holder["visual_store"] = VisualFileStore(EVAL_DIR / "visuals")
    holder["video_store"] = VideoFileStore(EVAL_DIR / "video")
    holder["subtitle_store"] = SubtitleFileStore(EVAL_DIR / "subtitles")
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

    data = track(["produce", "--project-id", str(project_id), "--target", "final"])
    gate_sequence.append(data["waiting_gate"])
    final_run_id = data["production_run_id"]
    qc_report_id = data["subject_artifact_id"]

    track(["approve", final_run_id, "--gate", "final"])
    final_data = track(["resume", final_run_id])

    resume_count = sum(1 for cmd in commands_run if cmd.startswith("motily resume"))
    executed_total = len(final_data.get("executed_nodes", []))
    reused_total = len(final_data.get("reused_nodes", []))

    qc_report = get_artifact(db_engine, project_id, MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCReport)

    report = {
        "commands": commands_run,
        "production_run_id": final_run_id,
        "target": "final",
        "gate_sequence": gate_sequence,
        "resume_count": resume_count,
        "exit_code_sequence": exit_codes,
        "executed_total": executed_total,
        "reused_total": reused_total,
        "final_status": final_data.get("status"),
        "qc_status": qc_report.overall_status.value,
        "video_path": str(holder["video_store"].root.resolve()),
        "subtitle_path": str(holder["subtitle_store"].root.resolve()),
        "network_calls": 0,
    }
    report_path = EVAL_DIR / REPORT_FILENAME
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"production_run_id: {final_run_id}")
    print(f"final_status:      {final_data.get('status')}")
    print(f"qc_status:         {qc_report.overall_status.value}")
    print(f"gate_sequence:     {gate_sequence}")
    print(f"report:            {report_path.resolve()}")

    cli_main._composition_override = None
    cli_main._context_factory_override = None

    return 0 if final_data.get("status") == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
