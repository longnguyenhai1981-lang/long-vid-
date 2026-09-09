"""Manual integration-evaluation utility for Phase 27: deterministic
timeline assembly.

Drives the real VisualRenderer.run() path (Phases 19-26) to produce three
real static visual frames -- a GENERATED_STILL background (via
FakeVisualProvider, never a live API), a DIAGRAM overlay (the real local
DiagramRenderer), and a COMPOSITION frame combining both (the real local
VisualLayerCompositor) -- then drives the real TimelineBuilder.run() path
(Phase 27) against a three-segment AssemblyPlan referencing them, with a
hand-authored VoiceRenderManifest pointing at three real local WAV files
(no live TTS API of any kind).

    SEG1 -> narration chunk 1 (2.0s) -> visual: V1 GENERATED_STILL ("frame A")
    SEG2 -> narration chunk 2 (1.5s) -> visual: V2 DIAGRAM ("frame B")
    SEG3 -> narration chunk 3 (2.5s) -> visual: V3 COMPOSITION ("frame C" =
             V1 as background + V2 as overlay)

The one-project chain this needs to reach MVP_COMPLETE is built directly
against a dedicated, disposable evaluation database
(data/timeline_builder_evaluation/eval.db, recreated fresh on every run)
-- never against the shared data/motily.db.

Usage:

    python scripts/evaluate_timeline_builder.py
"""

from __future__ import annotations

import sys
import wave
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

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
from app.layer_compositor.models import LayerAnchor, LayerScale
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.common import GateEvaluation, GateStatus, ModuleRunStatus, ProjectState
from app.models.diagram import DiagramCanvas, DiagramEllipse, DiagramPoint, DiagramSpec, DiagramStyle, DiagramTextLabel
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
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TimelineBuilderInput
from app.renderers.visual.models import (
    BackgroundLayerSource,
    CompositionSpec,
    OverlayLayerSource,
    VisualRendererInput,
)
from app.renderers.visual.renderer import VisualRenderer
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.audio.storage import AudioFileStore
from app.storage.artifacts import save_artifact
from app.storage.database import init_database
from app.storage.module_runs import save_module_run
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore

EVAL_DIR = Path("data/timeline_builder_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
BACKGROUND_SIZE = (640, 360)
BACKGROUND_COLOR = (30, 90, 150)
CHUNK_DURATIONS_SECONDS = {"C001": 2.0, "C002": 1.5, "C003": 2.5}


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]) -> None:
    now = datetime.now(timezone.utc)
    save_module_run(
        engine,
        ModuleRun(
            project_id=project_id, module=module, module_version="0.1", started_at=now,
            completed_at=now, input_ids=input_ids, status=ModuleRunStatus.SUCCESS,
        ),
    )


def _fake_still_response() -> VisualRenderResponse:
    image = Image.new("RGB", BACKGROUND_SIZE, BACKGROUND_COLOR)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return VisualRenderResponse(
        asset_bytes=buffer.getvalue(), provider="fake-visual", output_format="PNG",
        width=BACKGROUND_SIZE[0], height=BACKGROUND_SIZE[1],
    )


def _diagram_spec() -> DiagramSpec:
    return DiagramSpec(
        canvas=DiagramCanvas(width=300, height=200, background_color=None),
        elements=[
            DiagramEllipse(
                center=DiagramPoint(x=0.5, y=0.5), radius_x=0.4, radius_y=0.4,
                style=DiagramStyle(stroke_color="#FFFFFF", stroke_width=4, fill_color="#CC000080"),
            ),
            DiagramTextLabel(
                position=DiagramPoint(x=0.5, y=0.5), text="V2 DIAGRAM", align="CENTER",
                style=DiagramStyle(text_color="#FFFFFF", font_size=18),
            ),
        ],
    )


def _write_wav(path: Path, duration_seconds: float, framerate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = round(duration_seconds * framerate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(b"\x00\x00" * frame_count)


def _build_project_at_mvp_complete(engine) -> tuple[Project, ScriptPlan, VoicePlan, VisualPlan]:
    """The minimum project graph both VisualRenderer.run() and
    TimelineBuilder.run() require: three ScriptLines, three VoiceChunks,
    three VisualBeats (GENERATED_STILL, DIAGRAM, COMPOSITION). This is
    scaffolding to reach MVP_COMPLETE, not the thing being evaluated."""
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Phase 27 timeline assembly evaluation", created_at=created,
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

    voice_plan = VoicePlan(
        script_plan_id=script_plan.id,
        chunks=[
            VoiceChunk(chunk_id="C001", line_ids=["L001"], voice_state="NEUTRAL", pace="NORMAL", energy="MEDIUM", take_count=1, music_state="BED", sfx_opportunity="whoosh"),
            VoiceChunk(chunk_id="C002", line_ids=["L002"], voice_state="CURIOUS", pace="NORMAL", energy="MEDIUM", take_count=1, music_state="DUCK"),
            VoiceChunk(chunk_id="C003", line_ids=["L003"], voice_state="EXCITED", pace="FAST", energy="HIGH", take_count=1, music_state="LIFT"),
        ],
    )
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)

    visual_plan = VisualPlan(
        script_plan_id=script_plan.id, voice_plan_id=voice_plan.id,
        beats=[
            VisualBeat(
                beat_id="V1", script_line_ids=["L001"], narrative_node="Q0", visual_level="L1_ESTABLISH",
                visual_function="STORY", media_type="GENERATED_STILL", complexity="C1",
                concept="A wide shot of the Tacoma Narrows Bridge", primary_focus="The bridge deck",
            ),
            VisualBeat(
                beat_id="V2", script_line_ids=["L002"], narrative_node="Q0", visual_level="L1_ESTABLISH",
                visual_function="MECHANISM", media_type="DIAGRAM", complexity="C1",
                concept="A simple labeled diagram overlay", primary_focus="The diagram callout",
                diagram_spec=_diagram_spec(),
            ),
            VisualBeat(
                beat_id="V3", script_line_ids=["L003"], narrative_node="Q0", visual_level="L1_ESTABLISH",
                visual_function="STORY", media_type="COMPOSITION", complexity="C1",
                concept="Final composed frame: bridge background with diagram overlay",
                primary_focus="The complete composed shot",
            ),
        ],
    )
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE
    return project, script_plan, voice_plan, visual_plan


def _build_assembly_plan(script_plan, voice_plan, visual_plan) -> AssemblyPlan:
    return AssemblyPlan(
        script_plan_id=script_plan.id, voice_plan_id=voice_plan.id, visual_plan_id=visual_plan.id,
        estimated_total_duration_seconds=6.0,
        segments=[
            AssemblySegment(
                segment_id="S1", script_line_ids=["L001"], voice_chunk_ids=["C001"], visual_beat_id="V1",
                start_seconds=0.0, end_seconds=2.0, music_state="BED", transition_in="NONE", transition_out="CUT",
            ),
            AssemblySegment(
                segment_id="S2", script_line_ids=["L002"], voice_chunk_ids=["C002"], visual_beat_id="V2",
                start_seconds=2.0, end_seconds=3.5, music_state="DUCK", transition_in="CUT", transition_out="CUT",
            ),
            AssemblySegment(
                segment_id="S3", script_line_ids=["L003"], voice_chunk_ids=["C003"], visual_beat_id="V3",
                start_seconds=3.5, end_seconds=6.0, music_state="LIFT", transition_in="DISSOLVE", transition_out="NONE",
            ),
        ],
    )


def _build_voice_render_manifest(script_plan, voice_plan, audio_store: AudioFileStore) -> VoiceRenderManifest:
    renders = []
    for chunk in voice_plan.chunks:
        duration = CHUNK_DURATIONS_SECONDS[chunk.chunk_id]
        render_job_id = f"{chunk.chunk_id}_T1"
        _write_wav(audio_store.root / f"{render_job_id}.wav", duration_seconds=duration)
        renders.append(
            RenderedVoiceTake(
                render_job_id=render_job_id, chunk_id=chunk.chunk_id, take_number=1,
                line_ids=chunk.line_ids, file_path=f"{render_job_id}.wav", duration_seconds=duration,
                music_state=chunk.music_state, sfx_opportunity=chunk.sfx_opportunity,
            )
        )
    return VoiceRenderManifest(
        script_plan_id=script_plan.id, voice_plan_id=voice_plan.id, provider="local-test-fixture",
        voice_id="eval-voice", output_format="WAV", renders=renders, created_at=datetime.now(timezone.utc),
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if EVAL_DB_PATH.is_file():
        EVAL_DB_PATH.unlink()  # fresh, disposable DB every run
    engine = init_database(EVAL_DB_PATH)

    project, script_plan, voice_plan, visual_plan = _build_project_at_mvp_complete(engine)

    # --- Visual rendering: real VisualRenderer, FakeVisualProvider for V1,
    # real local DiagramRenderer for V2, real local VisualLayerCompositor
    # for V3 (background=V1, overlay=V2). No live API of any kind.
    provider = FakeVisualProvider([_fake_still_response()])
    visual_store = VisualFileStore(EVAL_DIR / "visuals")
    visual_renderer = VisualRenderer(engine, provider, VisualSettings(provider="none"), visual_store)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="background", source_beat_id="V1"),
            OverlayLayerSource(
                id="diagram_overlay", source_beat_id="V2", z_index=1,
                anchor=LayerAnchor.TOP_LEFT, scale=LayerScale(relative_height=0.4), margin=16,
            ),
        ]
    )
    visual_result = visual_renderer.run(
        VisualRendererInput(project_id=project.project_id, composition_specs={"V3": composition_spec})
    )

    # --- AssemblyPlan + VoiceRenderManifest: hand-authored, deterministic,
    # local WAV fixtures -- no live TTS API.
    assembly_plan = _build_assembly_plan(script_plan, voice_plan, visual_plan)
    save_artifact(engine, project.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(EVAL_DIR / "audio")
    voice_render_manifest = _build_voice_render_manifest(script_plan, voice_plan, audio_store)
    save_artifact(engine, project.project_id, "voice_render_manifest", voice_render_manifest)

    # --- Timeline assembly: the real TimelineBuilder.
    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_result = timeline_builder.run(TimelineBuilderInput(project_id=project.project_id))
    manifest = timeline_result.manifest

    print(f"Evaluation project: {project.project_id}")
    print(f"Visual provider calls: {visual_result.provider_call_count} (expected 1, only for V1)")
    print()
    print(f"{'segment_id':<10} {'start_ms':>9} {'end_ms':>9} {'duration_ms':>12}  {'voice_ref':<12} visual_ref")
    for segment in manifest.segments:
        voice_ref = "+".join(ref.render_job_id for ref in segment.narration)
        if segment.visual.status.value == "RENDERED":
            visual_ref = f"{segment.visual.media_type.value}:{segment.visual.file_path}"
        else:
            visual_ref = f"{segment.visual.media_type.value}:{segment.visual.reference}"
        print(
            f"{segment.segment_id:<10} {segment.start_ms:>9} {segment.end_ms:>9} "
            f"{segment.duration_ms:>12}  {voice_ref:<12} {visual_ref}"
        )
    print()
    print(f"Total duration: {manifest.total_duration_ms}ms")
    print()
    print("Cue events:")
    for cue in manifest.cues:
        ref = f" ({cue.reference})" if cue.reference else ""
        print(f"  {cue.timestamp_ms:>6}ms  {cue.cue_type.value}{ref}  segment={cue.segment_id}")
    print()
    print(f"Segment count: {len(manifest.segments)}")
    print(f"Cue count: {len(manifest.cues)}")
    print(f"Timeline manifest id: {manifest.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
