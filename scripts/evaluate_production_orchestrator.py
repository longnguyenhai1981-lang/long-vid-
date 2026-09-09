"""Manual integration-evaluation utility for Phase 33: the deterministic
production orchestrator and its approval gate.

Builds a disposable local project up to MVP_COMPLETE with a ScriptPlan/
VoicePlan/VisualPlan/AssemblyPlan already saved (the same upstream
scaffolding every other evaluate_*.py script builds by hand), then hands
control to the REAL ProductionRunner (app/orchestration/runner.py)
wired to the REAL VOICE_RENDER/VISUAL_RENDER/TIMELINE/VIDEO_RENDER/
CAPTION_BUILD/SUBTITLE_EXPORT/MEDIA_QC adapters
(app/orchestration/adapters.py). FakeTTSProvider/FakeVisualProvider
stand in for network TTS/image providers; every other step is the real
local implementation (ffmpeg encode, ffprobe QC inspection, SRT export,
caption burn-in) -- no live API calls anywhere in this script.

Run 1 drives the runner to MEDIA_QC: every wired node executes once,
QC passes/warns, and the run stops WAITING_APPROVAL at
FINAL_MEDIA_APPROVAL. This script then calls approve_gate() for the
exact final-media artifact and calls resume() -- run 2 reuses every
fresh upstream artifact (no re-execution) and reaches SUCCEEDED.

Usage:

    python scripts/evaluate_production_orchestrator.py
"""

from __future__ import annotations

import io
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
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.models import SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.common import GateEvaluation, GateStatus, ModuleRunStatus, ProjectState
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.models.visual import VisualBeat, VisualPlan
from app.models.voice import VoiceChunk, VoicePlan
from app.orchestration.adapters import build_default_adapters, build_default_graph
from app.orchestration.gates import approve_gate
from app.orchestration.models import ApprovalGateType, ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.artifacts import save_artifact
from app.storage.database import init_database
from app.storage.module_runs import save_module_run
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore

EVAL_DIR = Path("data/production_orchestrator_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"


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


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]) -> None:
    now = datetime.now(timezone.utc)
    save_module_run(
        engine,
        ModuleRun(
            project_id=project_id, module=module, module_version="0.1", started_at=now,
            completed_at=now, input_ids=input_ids, status=ModuleRunStatus.SUCCESS,
        ),
    )


def _build_project_at_mvp_complete(engine) -> tuple[Project, ScriptPlan]:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Phase 33 orchestrator evaluation", created_at=created,
        updated_at=created, state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    project_id = project.project_id

    idea = IdeaCandidate(
        topic="Tacoma Narrows Bridge",
        central_question="Why did a sturdy bridge collapse in mild wind?",
        abt=ABT(
            and_context="Engineers believed the bridge was safe",
            but_complication="It oscillated violently and collapsed in moderate wind",
            therefore_investigation="Investigate the hidden aerodynamic mechanism",
        ),
        primary_payoff="REVERSAL", physics_core="Self-excited aeroelastic flutter",
        audience_prerequisite="none",
        brand_fit=GateEvaluation(status="PASS", reason="ok"),
        general_audience_gate=GateEvaluation(status="PASS", reason="ok"),
        longform_potential=GateEvaluation(status="PASS", reason="ok"),
    )
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    approve_idea(engine, project_id)

    research_r0 = ResearchR0(
        idea_id=idea.id, topic_valid=True, credible_sources_available=True,
        story_material_available=True, physics_material_available=True, recommendation="CONTINUE",
    )
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = FeasibilityReport(
        status="PASS", audience=SubEvaluation(status="PASS", reason="r"),
        science=SubEvaluation(status="PASS", reason="r"), narrative=SubEvaluation(status="PASS", reason="r"),
        visual=SubEvaluation(status="PASS", reason="r"),
        production=ProductionEvaluation(status="PASS", estimated_complexity="LOW", reason="r"),
    )
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    decide_feasibility(engine, project_id, GateStatus.PASS)

    research_package = ResearchPackage(
        central_question=idea.central_question, executive_summary="Flutter caused the bridge to collapse.",
        physics_core="Self-excited aeroelastic flutter",
        simplification_boundary="1. safe_model: ... 2. allowed_simplifications: ...",
        claims=[Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")],
    )
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)

    narrative_plan = NarrativePlan(
        central_question=idea.central_question,
        scqa=SCQA(
            situation="A bridge opened to fanfare", complication="It oscillated wildly in ordinary wind",
            question="Why would this happen?", answer="Self-excited aerodynamic flutter",
        ),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(
                id="Q0", question="Why did it twist?", why_viewer_cares="It matters",
                partial_answer="Flutter", claim_ids=["C001"], creates_next_question=None,
                information_gap="none left",
            )
        ],
        ti_role="Investigator", ending="Callback to the opening image", claim_ids_used=["C001"],
    )
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, narrative_plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", narrative_plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    approve_narrative(engine, project_id)

    packaging = PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism", risk_of_misleading="LOW",
    )
    save_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, packaging)
    update_artifact_reference(engine, project_id, "packaging_prototype_id", packaging.id)
    _record_module_run_success(
        engine, project_id, "packaging_p0_engine",
        [str(idea.id), str(research_package.id), str(narrative_plan.id)],
    )
    approve_packaging_p0(engine, project_id)

    script_plan = ScriptPlan(
        estimated_duration_seconds=8,
        beats=[
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM",
                lines=[
                    ScriptLine(line_id="L001", text="Đây là câu L001.", function="INFORM"),
                    ScriptLine(line_id="L002", text="Đây là câu L002.", function="INFORM"),
                    ScriptLine(line_id="L003", text="Đây là câu L003.", function="INFORM"),
                    ScriptLine(line_id="L004", text="Đây là câu L004.", function="INFORM"),
                ],
            )
        ],
        qa_status="PASS",
    )
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan.id)
    update_project_state(engine, project_id, ProjectState.SCRIPT_VERIFICATION)

    verification_report = ScriptVerificationReport(status="PASS")
    save_artifact(engine, project_id, SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE, verification_report)
    _record_module_run_success(
        engine, project_id, "script_verification_engine",
        [str(research_package.id), str(narrative_plan.id), str(packaging.id), str(script_plan.id)],
    )
    accept_script_verification(engine, project_id)
    approve_final_script(engine, project_id)

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE
    return project, script_plan


def _build_ready_project(engine) -> Project:
    """MVP_COMPLETE plus a real VoicePlan/VisualPlan/AssemblyPlan --
    everything the unwired (LLM-based) creative chain would have
    produced, stopping exactly at the boundary Phase 33 orchestrates."""
    project, script_plan = _build_project_at_mvp_complete(engine)

    voice_plan = VoicePlan(
        script_plan_id=script_plan.id,
        chunks=[
            VoiceChunk(chunk_id="C001", line_ids=["L001", "L002"], voice_state="NEUTRAL", pace="NORMAL", energy="MEDIUM", take_count=1, music_state="BED"),
            VoiceChunk(chunk_id="C002", line_ids=["L003", "L004"], voice_state="CURIOUS", pace="NORMAL", energy="MEDIUM", take_count=1, music_state="DUCK"),
        ],
    )
    save_artifact(engine, project.project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)

    visual_plan = VisualPlan(
        script_plan_id=script_plan.id, voice_plan_id=voice_plan.id,
        beats=[
            VisualBeat(beat_id="V1", script_line_ids=["L001", "L002"], narrative_node="Q0", visual_level="L1_ESTABLISH", visual_function="STORY", media_type="GENERATED_STILL", complexity="C1", concept="c1", primary_focus="f1"),
            VisualBeat(beat_id="V2", script_line_ids=["L003", "L004"], narrative_node="Q0", visual_level="L1_ESTABLISH", visual_function="STORY", media_type="GENERATED_STILL", complexity="C1", concept="c2", primary_focus="f2"),
        ],
    )
    save_artifact(engine, project.project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    assembly_plan = AssemblyPlan(
        script_plan_id=script_plan.id, voice_plan_id=voice_plan.id, visual_plan_id=visual_plan.id,
        estimated_total_duration_seconds=4.0,
        segments=[
            AssemblySegment(segment_id="S1", script_line_ids=["L001", "L002"], voice_chunk_ids=["C001"], visual_beat_id="V1", start_seconds=0.0, end_seconds=2.0, music_state="BED", transition_in="NONE", transition_out="CUT"),
            AssemblySegment(segment_id="S2", script_line_ids=["L003", "L004"], voice_chunk_ids=["C002"], visual_beat_id="V2", start_seconds=2.0, end_seconds=4.0, music_state="DUCK", transition_in="CUT", transition_out="NONE"),
        ],
    )
    save_artifact(engine, project.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    return project


def _build_ctx(engine, project_id: UUID) -> ExecutionContext:
    return ExecutionContext(
        db_engine=engine,
        project_id=project_id,
        audio_store=AudioFileStore(EVAL_DIR / "audio"),
        visual_store=VisualFileStore(EVAL_DIR / "visuals"),
        video_store=VideoFileStore(EVAL_DIR / "video"),
        subtitle_store=SubtitleFileStore(EVAL_DIR / "subtitles"),
        tts_provider=FakeTTSProvider(
            [TTSResponse(audio_bytes=_real_wav_bytes(2.0), provider="fake-tts", audio_format="WAV") for _ in range(10)]
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


def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0] if result.returncode == 0 else "(unavailable)"


def _print_trace(label: str, run) -> None:
    print(f"--- {label} (status={run.status.value}, stop_reason={run.stop_reason}) ---")
    print(f"{'node':<16} {'status':<18} {'exec/reuse':<12} artifact_id")
    for node_id, record in run.node_states.items():
        mode = "executed" if record.executed_this_run else ("reused" if record.reused_existing_artifact else "-")
        print(f"{node_id:<16} {record.status.value:<18} {mode:<12} {record.artifact_id}")
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

    project = _build_ready_project(engine)
    ctx = _build_ctx(engine, project.project_id)

    graph = build_default_graph()
    adapters = build_default_adapters()
    runner = ProductionRunner(engine, graph, adapters)

    first = runner.run_until(project.project_id, "MEDIA_QC", ctx=ctx)
    _print_trace("Run 1 (fresh execution)", first)

    executed_count_first = sum(1 for r in first.node_states.values() if r.executed_this_run)
    reused_count_first = sum(1 for r in first.node_states.values() if r.reused_existing_artifact)

    if first.status is not ProductionRunStatus.WAITING_APPROVAL:
        print(f"UNEXPECTED: run 1 ended {first.status.value}, not WAITING_APPROVAL.", file=sys.stderr)
        return 1

    qc_record = first.node_states["MEDIA_QC"]
    final_media_artifact_id = UUID(qc_record.artifact_id)
    approve_gate(engine, graph, project.project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, final_media_artifact_id)

    resumed = runner.resume(first.id, ctx=ctx)
    _print_trace("Run 2 (after approval, resume)", resumed)

    executed_count_second = sum(1 for r in resumed.node_states.values() if r.executed_this_run)
    reused_count_second = sum(1 for r in resumed.node_states.values() if r.reused_existing_artifact)

    print(f"production_run_id:            {first.id}")
    print(f"first-run status:             {first.status.value}")
    print(f"final status:                 {resumed.status.value}")
    print(f"executed node count (run 1):  {executed_count_first}")
    print(f"reused node count (resume):   {reused_count_second}")
    print(f"QC status:                    {qc_record.status.value} (report artifact reused={reused_count_first > 0})")
    print(f"final media artifact id:      {final_media_artifact_id}")
    print(f"approval gate subject id:     {final_media_artifact_id}")
    print(f"FFmpeg version:               {_ffmpeg_version()}")

    if resumed.status is not ProductionRunStatus.SUCCEEDED:
        print("UNEXPECTED: resume did not reach SUCCEEDED.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
