"""Manual integration-evaluation utility for Phase 34: the complete
production pipeline (IDEA through FINAL_MEDIA_APPROVAL) driven end to
end through ProductionRunner only.

This does NOT replace scripts/evaluate_production_orchestrator.py
(Phase 33's own downstream-slice evaluation, preserved unchanged as
historical reference) -- this script proves the FULL, now fully-wired
19-node graph, stopping at every one of the six real human approval
gates and resuming past each with a real app/review/service.py call (or
app/orchestration/gates.py's approve_gate for the one new
FINAL_MEDIA_APPROVAL gate).

FakeLLMProvider/FakeResearchRetriever stand in for the twelve upstream
engines' own network calls; FakeTTSProvider/FakeVisualProvider stand in
for the seven downstream renderers' own network calls; everything else
(ffmpeg encode, ffprobe QC inspection, SRT export, caption burn-in) is
the real local implementation. No live network call anywhere.

Usage:

    python scripts/evaluate_full_production_orchestrator.py
"""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.audio.config import TTSSettings
from app.audio.fake import FakeTTSProvider
from app.audio.models import TTSResponse
from app.audio.storage import AudioFileStore
from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
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
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.orchestration.gates import approve_gate
from app.orchestration.models import ApprovalGateType, ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE
from app.research.fake import FakeResearchRetriever
from app.research.models import ResearchQuery, ResearchSearchResponse, RetrievedSource
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

EVAL_DIR = Path("data/full_orchestrator_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
REPORT_FILENAME = "motily_phase34_report.json"


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


# ---------------------------------------------------------------------------
# Canned LLM JSON fixtures for the twelve upstream engines (no live API)
# ---------------------------------------------------------------------------

_IDEA_JSON = json.dumps(
    {
        "topic": "Tacoma Narrows Bridge",
        "central_question": "Why did a sturdy bridge collapse in mild wind?",
        "abt": {
            "and_context": "Engineers believed the bridge was safe",
            "but_complication": "It oscillated violently and collapsed in moderate wind",
            "therefore_investigation": "Investigate the hidden aerodynamic mechanism",
        },
        "primary_payoff": "REVERSAL",
        "secondary_payoffs": [],
        "physics_core": "Self-excited aeroelastic flutter",
        "audience_prerequisite": "none",
        "brand_fit": {"status": "PASS", "reason": "ok"},
        "general_audience_gate": {"status": "PASS", "reason": "ok"},
        "longform_potential": {"status": "PASS", "reason": "ok"},
        "research_questions": ["How does flutter work?"],
        "risks": ["Confusing it with mechanical resonance"],
    }
)


def _research_r0_json() -> str:
    return json.dumps(
        {
            "idea_id": str(UUID(int=0)),  # deterministically overridden by the engine
            "topic_valid": True,
            "credible_sources_available": True,
            "story_material_available": True,
            "physics_material_available": True,
            "initial_findings": ["Aeroelastic flutter is a documented bridge failure mode"],
            "candidate_sources": ["https://real.example/source"],
            "major_risks": [],
            "recommendation": "CONTINUE",
        }
    )


def _feasibility_json() -> str:
    axis = {"status": "PASS", "reason": "ok"}
    return json.dumps(
        {
            "status": "PASS",
            "audience": axis, "science": axis, "narrative": axis, "visual": axis,
            "production": {"status": "PASS", "estimated_complexity": "LOW", "reason": "ok"},
            "likely_reusable_assets": [], "likely_expensive_scenes": [], "suggested_reframes": [],
        }
    )


def _research_r1_json() -> str:
    return json.dumps(
        {
            "central_question": "placeholder",  # deterministically overridden
            "executive_summary": "Flutter caused the bridge to collapse.",
            "timeline": ["1940: bridge collapses"],
            "physics_core": "Self-excited aeroelastic flutter",
            "claims": [
                {
                    "claim_id": "C001", "claim": "Flutter is self-excited", "status": "SAFE",
                    "confidence": "HIGH", "source_ids": ["S001"], "qualification": None, "notes": None,
                }
            ],
            "disputed_points": [], "misconceptions": [],
            "simplification_boundary": (
                "1. safe_model: flutter as self-reinforcing oscillation. "
                "2. allowed_simplifications: skip full differential equations."
            ),
            "prohibited_claims": [],
            "sources": [
                {
                    "source_id": "S001", "title": "Real Paper", "url": "https://real.example/source",
                    "type": "paper", "quality_tier": 1, "authoritative": True, "supports_claims": ["C001"],
                }
            ],
        }
    )


def _narrative_json() -> str:
    return json.dumps(
        {
            "central_question": "placeholder",  # deterministically overridden
            "scqa": {
                "situation": "A bridge opened to fanfare",
                "complication": "It oscillated wildly in ordinary wind",
                "question": "Why would this happen?",
                "answer": "Self-excited aerodynamic flutter",
            },
            "opening": "MYSTERY_FIRST",
            "question_ladder": [
                {
                    "id": "Q0", "question": "Why did it twist?", "why_viewer_cares": "It matters",
                    "partial_answer": "Flutter", "claim_ids": ["C001"], "creates_next_question": None,
                    "information_gap": "none left",
                }
            ],
            "ti_role": "Investigator", "physics_entry_points": [], "deep_dive_points": [], "breath_moments": [],
            "ending": "Callback to the opening image", "claim_ids_used": ["C001"],
        }
    )


def _packaging_p0_json(risk: str = "LOW") -> str:
    return json.dumps(
        {
            "promise": "A sturdy bridge tore itself apart in ordinary wind",
            "title_direction": "The bridge that shook itself to pieces",
            "thumbnail_conflict": "CAN WIND REALLY DO THIS?",
            "viewer_expectation": "An investigation into a real aerodynamic mechanism",
            "risk_of_misleading": risk,
        }
    )


def _script_json() -> str:
    def line(line_id: str) -> dict:
        return {"line_id": line_id, "text": f"Đây là câu {line_id}.", "function": "INFORM", "claim_ids": ["C001"]}

    return json.dumps(
        {
            "estimated_duration_seconds": 480,
            "beats": [
                {
                    "beat_id": "B001", "narrative_node": "Q0", "narrative_function": "INFORM",
                    "lines": [line("L001"), line("L002")],
                },
                {
                    "beat_id": "B002", "narrative_node": "Q0", "narrative_function": "REVEAL",
                    "lines": [line("L003"), line("L004")],
                },
            ],
            "claim_coverage": ["C001"], "unmapped_claims": [], "qa_status": "PASS",
        }
    )


def _script_verification_json() -> str:
    return json.dumps(
        {"status": "PASS", "unsupported_lines": [], "overstated_lines": [], "dangerous_simplifications": [], "recommended_rewrites": []}
    )


def _voice_plan_json(script_plan_id) -> str:
    def chunk(chunk_id, line_ids, music_state="BED"):
        return {
            "chunk_id": chunk_id, "line_ids": line_ids, "voice_state": "NEUTRAL", "pace": "NORMAL",
            "energy": "MEDIUM", "take_count": 1, "music_state": music_state, "sfx_opportunity": None,
        }

    return json.dumps(
        {
            "script_plan_id": str(script_plan_id),
            "chunks": [chunk("C001", ["L001", "L002"]), chunk("C002", ["L003", "L004"], "DUCK")],
        }
    )


def _visual_plan_json(script_plan_id, voice_plan_id) -> str:
    def beat(beat_id, line_ids):
        return {
            "beat_id": beat_id, "script_line_ids": line_ids, "narrative_node": "Q0",
            "visual_level": "L1_ESTABLISH", "visual_function": "STORY", "media_type": "GENERATED_STILL",
            "complexity": "C1", "concept": f"Concept for {beat_id}", "primary_focus": f"Focus for {beat_id}",
            "evidence_source_ids": [],
        }

    return json.dumps(
        {
            "script_plan_id": str(script_plan_id), "voice_plan_id": str(voice_plan_id),
            "beats": [beat("VB001", ["L001", "L002"]), beat("VB002", ["L003", "L004"])],
        }
    )


def _assembly_plan_json(script_plan_id, voice_plan_id, visual_plan_id) -> str:
    def segment(segment_id, line_ids, chunk_ids, beat_id, start, end):
        return {
            "segment_id": segment_id, "script_line_ids": line_ids, "voice_chunk_ids": chunk_ids,
            "visual_beat_id": beat_id, "start_seconds": start, "end_seconds": end,
            "music_state": "BED", "transition_in": "CUT", "transition_out": "CUT",
        }

    return json.dumps(
        {
            "script_plan_id": str(script_plan_id), "voice_plan_id": str(voice_plan_id),
            "visual_plan_id": str(visual_plan_id), "estimated_total_duration_seconds": 480.0,
            "segments": [
                segment("S001", ["L001", "L002"], ["C001"], "VB001", 0.0, 240.0),
                segment("S002", ["L003", "L004"], ["C002"], "VB002", 240.0, 480.0),
            ],
        }
    )


def _packaging_p1_json(packaging_prototype_id, script_plan_id, visual_plan_id, assembly_plan_id) -> str:
    return json.dumps(
        {
            "packaging_prototype_id": str(packaging_prototype_id), "script_plan_id": str(script_plan_id),
            "visual_plan_id": str(visual_plan_id), "assembly_plan_id": str(assembly_plan_id),
            "title": "Chiếc cầu tự xé nát chính nó", "thumbnail_text": "CHỈ VÌ GIÓ?",
            "thumbnail_concept": "Bridge deck twisting sharply.",
            "final_promise": "A sturdy bridge tore itself apart in ordinary wind.",
            "expected_payoff": "The real aerodynamic mechanism, not resonance.",
            "viewer_expectation": "An honest investigation into a real physical mechanism.",
            "rationale": "Sharpens P0's promise now the reveal is locked into the script.",
            "risk_of_misleading": "LOW",
        }
    )


def _llm_settings():
    from app.llm.config import LLMSettings

    return LLMSettings(provider="fake", default_model="fake-model", max_structured_retries=2)


def _new_bare_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Phase 34 full orchestrator evaluation", created_at=created,
        updated_at=created, state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    update_project_state(engine, project.project_id, ProjectState.IDEA_DISCOVERY)
    return project.project_id


def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0] if result.returncode == 0 else "(unavailable)"


def _trace_rows(run) -> list[dict]:
    return [
        {
            "node": node_id,
            "status": record.status.value,
            "executed": record.executed_this_run,
            "reused": record.reused_existing_artifact,
            "artifact_id": record.artifact_id,
            "reason": record.reason.value if record.reason else None,
        }
        for node_id, record in run.node_states.items()
    ]


def _print_trace(label: str, run) -> None:
    print(f"--- {label} (status={run.status.value}, waiting_gate={run.waiting_gate}) ---")
    print(f"{'node':<16} {'status':<18} {'exec/reuse':<10} {'reason':<24} artifact_id")
    for row in _trace_rows(run):
        mode = "executed" if row["executed"] else ("reused" if row["reused"] else "-")
        print(f"{row['node']:<16} {row['status']:<18} {mode:<10} {str(row['reason']):<24} {row['artifact_id']}")
    print()


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
    engine = init_database(EVAL_DB_PATH)
    project_id = _new_bare_project(engine)

    graph = build_full_graph()
    adapters = build_full_adapters()
    runner = ProductionRunner(engine, graph, adapters)

    llm_settings = _llm_settings()
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=llm_settings,
        research_retriever=FakeResearchRetriever([_evidence() for _ in range(30)]),
        initial_idea_input=IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN),
    )

    gate_sequence: list[str] = []
    resume_count = 0

    ctx.llm_provider = FakeLLMProvider([_response(_IDEA_JSON)])
    run = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx)
    _print_trace("RUN 1: IDEA", run)
    gate_sequence.append(run.waiting_gate)
    approve_idea(engine, project_id)

    ctx.llm_provider = FakeLLMProvider([_response(_research_r0_json()), _response(_feasibility_json())])
    run = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 2: RESEARCH_R0 -> FEASIBILITY", run)
    gate_sequence.append(run.waiting_gate)
    decide_feasibility(engine, project_id, GateStatus.PASS)

    ctx.llm_provider = FakeLLMProvider([_response(_research_r1_json()), _response(_narrative_json())])
    run = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 3: RESEARCH_R1 -> NARRATIVE", run)
    gate_sequence.append(run.waiting_gate)
    approve_narrative(engine, project_id)

    ctx.llm_provider = FakeLLMProvider([_response(_packaging_p0_json())])
    run = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 4: PACKAGING_P0", run)
    gate_sequence.append(run.waiting_gate)
    approve_packaging_p0(engine, project_id)

    ctx.llm_provider = FakeLLMProvider([_response(_script_json()), _response(_script_verification_json())])
    run = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 5: SCRIPT -> SCRIPT_VERIFY", run)
    gate_sequence.append(run.waiting_gate)
    accept_script_verification(engine, project_id)
    approve_final_script(engine, project_id)

    run = runner.run_until(project_id, "SCRIPT_VERIFY", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 6: upstream creative chain SUCCEEDED", run)

    script_plan = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    ctx.llm_provider = FakeLLMProvider([_response(_voice_plan_json(script_plan.id)) for _ in range(5)])
    run = runner.run_until(project_id, "VOICE_PLAN", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 7: VOICE_PLAN", run)

    voice_plan = get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
    ctx.llm_provider = FakeLLMProvider(
        [_response(_visual_plan_json(script_plan.id, voice_plan.id)) for _ in range(5)]
    )
    run = runner.run_until(project_id, "VISUAL_PLAN", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 8: VISUAL_PLAN", run)

    visual_plan = get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
    ctx.llm_provider = FakeLLMProvider(
        [_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id)) for _ in range(5)]
    )
    run = runner.run_until(project_id, "ASSEMBLY_PLAN", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 9: ASSEMBLY_PLAN", run)

    assembly_plan = get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
    packaging = get_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
    ctx.llm_provider = FakeLLMProvider(
        [
            _response(_packaging_p1_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id))
            for _ in range(5)
        ]
    )
    run = runner.run_until(project_id, "PACKAGING_P1", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 10: PACKAGING_P1", run)

    ctx.audio_store = AudioFileStore(EVAL_DIR / "audio")
    ctx.visual_store = VisualFileStore(EVAL_DIR / "visuals")
    ctx.video_store = VideoFileStore(EVAL_DIR / "video")
    ctx.subtitle_store = SubtitleFileStore(EVAL_DIR / "subtitles")
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

    run = runner.run_until(project_id, "MEDIA_QC", ctx=ctx, run_id=run.id)
    resume_count += 1
    _print_trace("RUN 11: downstream slice -> MEDIA_QC", run)
    gate_sequence.append(run.waiting_gate)

    qc_report_id = UUID(run.node_states["MEDIA_QC"].artifact_id)
    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, qc_report_id)
    final_run = runner.resume(run.id, ctx=ctx)
    resume_count += 1
    _print_trace("RUN 12: final resume -> SUCCEEDED", final_run)

    qc_report = get_artifact(engine, project_id, MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCReport)
    final_packaging = get_artifact(engine, project_id, "packaging_p1", FinalPackagingPlan)
    video_path = ctx.video_store.root
    subtitle_path = ctx.subtitle_store.root

    executed_total = sum(1 for r in run.node_states.values() if r.executed_this_run) + sum(
        1 for r in final_run.node_states.values() if r.executed_this_run
    )
    reuse_total = sum(1 for r in final_run.node_states.values() if r.reused_existing_artifact)

    summary = {
        "production_run_id": str(final_run.id),
        "registered_nodes": sorted(graph.node_ids()),
        "wired_nodes": sorted(adapters.keys()),
        "gate_sequence": gate_sequence,
        "resume_count": resume_count,
        "executed_node_total": executed_total,
        "reuse_total": reuse_total,
        "final_status": final_run.status.value,
        "qc_status": qc_report.overall_status.value,
        "final_media_artifact_id": str(qc_report_id),
        "final_video_dir": str(video_path.resolve()),
        "subtitle_dir": str(subtitle_path.resolve()),
        "final_approval_subject_id": str(qc_report_id),
        "network_calls": 0,
    }

    report = {
        "graph_nodes": sorted(graph.node_ids()),
        "gate_sequence": gate_sequence,
        "artifact_ids": {
            "script_plan_id": str(script_plan.id),
            "voice_plan_id": str(voice_plan.id),
            "visual_plan_id": str(visual_plan.id),
            "assembly_plan_id": str(assembly_plan.id),
            "final_packaging_plan_id": str(final_packaging.id),
            "media_qc_report_id": str(qc_report_id),
        },
        "statuses": {node_id: record.status.value for node_id, record in final_run.node_states.items()},
        "final_media_path": str(video_path.resolve()),
        "qc_result": {
            "overall_status": qc_report.overall_status.value,
            "ready_for_human_review": qc_report.ready_for_human_review,
        },
        "summary": summary,
    }
    report_path = EVAL_DIR / REPORT_FILENAME
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"production_run_id:          {summary['production_run_id']}")
    print(f"registered_nodes:           {len(summary['registered_nodes'])}")
    print(f"wired_nodes:                {len(summary['wired_nodes'])}")
    print(f"gate_sequence:              {summary['gate_sequence']}")
    print(f"resume_count:               {summary['resume_count']}")
    print(f"executed_node_total:        {summary['executed_node_total']}")
    print(f"reuse_total (final resume): {summary['reuse_total']}")
    print(f"final_production_status:    {summary['final_status']}")
    print(f"QC status:                  {summary['qc_status']}")
    print(f"final_video_dir:            {summary['final_video_dir']}")
    print(f"subtitle_dir:               {summary['subtitle_dir']}")
    print(f"final_approval_subject_id:  {summary['final_approval_subject_id']}")
    print(f"network_calls:              {summary['network_calls']}")
    print(f"FFmpeg version:             {_ffmpeg_version()}")
    print(f"Report JSON:                {report_path.resolve()}")

    if final_run.status.value != "SUCCEEDED":
        print("UNEXPECTED: final run did not reach SUCCEEDED.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
