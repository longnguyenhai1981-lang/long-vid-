"""Manual integration-evaluation utility for Phase 24: beat-to-beat visual
composition.

Unlike scripts/evaluate_visual_renderer_ti_state_integration.py (Phase 23,
which proves the VisualRenderer <-> TiCompositor wiring itself with an
explicit background_path), this script proves Phase 24's new
background_beat_id path: a TI_STATE COMPOSITE beat consuming ANOTHER
beat's own rendered output as its background, with zero background_path
supplied anywhere.

The VisualPlan here deliberately authors V2 (TI_STATE, COMPOSITE,
background_beat_id="V1") BEFORE V1 (GENERATED_STILL) in its beat list --
the opposite of rendering order -- specifically so this script proves the
renderer's dependency ordering is real (V1 actually renders first) and
not an accident of iterating visual_plan.beats in file order.

Uses the REAL active v1 TiAssetSet (read from data/motily.db) for the
canonical Tí asset. V1's "provider" output is produced by
FakeVisualProvider with a real, Pillow-decodable fixture image -- never a
live image-generation API call, per this phase's instructions ("do NOT
generate a live background"). Any accidental extra provider call would
raise immediately, since FakeVisualProvider is queued with exactly one
response.

The one-project chain this needs to reach MVP_COMPLETE is built directly
against a dedicated, disposable evaluation database
(data/visual_renderer_beat_composition_evaluation/eval.db, recreated
fresh on every run) -- never against the shared data/motily.db.

Usage:

    python scripts/evaluate_visual_renderer_beat_composition.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

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
from app.renderers.visual.models import TiStateRenderMode, TiStateSource, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.artifacts import save_artifact
from app.storage.database import DEFAULT_DB_PATH, init_database
from app.storage.errors import TiAssetSetNotFoundError
from app.storage.module_runs import save_module_run
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state
from app.storage.ti_assets import get_active_ti_asset_set, save_ti_asset_set
from app.ti_assets.retriever import SqliteTiAssetRetriever
from app.ti_assets.storage import DEFAULT_TI_ASSET_ROOT, TiAssetFileStore
from app.ti_compositor.compositor import TiCompositor
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore

EVAL_DIR = Path("data/visual_renderer_beat_composition_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
BACKGROUND_TI_STATE = "SKEPTICAL"
BACKGROUND_SIZE = (640, 360)
BACKGROUND_COLOR = (30, 90, 150)


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]) -> None:
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


def _fake_still_response() -> VisualRenderResponse:
    """A real, Pillow-decodable PNG -- not FakeVisualProvider's usual
    placeholder bytes -- since Phase 24's TiCompositor step must actually
    open V1's rendered output as a background image."""
    image = Image.new("RGB", BACKGROUND_SIZE, BACKGROUND_COLOR)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return VisualRenderResponse(
        asset_bytes=buffer.getvalue(),
        provider="fake-visual",
        output_format="PNG",
        width=BACKGROUND_SIZE[0],
        height=BACKGROUND_SIZE[1],
    )


def _build_two_beat_project_at_mvp_complete(engine) -> tuple[Project, ScriptPlan, VoicePlan, VisualPlan]:
    """The minimum project graph VisualRenderer.run() requires, with a
    two-beat VisualPlan: V2 (TI_STATE COMPOSITE, background_beat_id="V1")
    authored FIRST, V1 (GENERATED_STILL) authored SECOND -- the reverse of
    rendering order, deliberately, per this phase's evaluation
    instructions. Mirrors
    scripts/evaluate_visual_renderer_ti_state_integration.py's fixture
    builder, trimmed and adapted for two beats -- this is scaffolding to
    reach MVP_COMPLETE, not the thing being evaluated."""
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Phase 24 beat-to-beat composition evaluation",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
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
        primary_payoff="REVERSAL",
        physics_core="Self-excited aeroelastic flutter",
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
        idea_id=idea.id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        recommendation="CONTINUE",
    )
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = FeasibilityReport(
        status="PASS",
        audience=SubEvaluation(status="PASS", reason="r"),
        science=SubEvaluation(status="PASS", reason="r"),
        narrative=SubEvaluation(status="PASS", reason="r"),
        visual=SubEvaluation(status="PASS", reason="r"),
        production=ProductionEvaluation(status="PASS", estimated_complexity="LOW", reason="r"),
    )
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    decide_feasibility(engine, project_id, GateStatus.PASS)

    research_package = ResearchPackage(
        central_question=idea.central_question,
        executive_summary="Flutter caused the bridge to collapse.",
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
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, narrative_plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", narrative_plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    approve_narrative(engine, project_id)

    packaging = PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading="LOW",
    )
    save_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, packaging)
    update_artifact_reference(engine, project_id, "packaging_prototype_id", packaging.id)
    _record_module_run_success(
        engine, project_id, "packaging_p0_engine",
        [str(idea.id), str(research_package.id), str(narrative_plan.id)],
    )
    approve_packaging_p0(engine, project_id)

    script_plan = ScriptPlan(
        estimated_duration_seconds=60,
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q0",
                narrative_function="INFORM",
                lines=[ScriptLine(line_id="L001", text="Đây là câu L001.", function="INFORM")],
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
            VoiceChunk(
                chunk_id="C001", line_ids=["L001"], voice_state="NEUTRAL", pace="NORMAL",
                energy="MEDIUM", take_count=1, music_state="BED",
            )
        ],
    )
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)

    # V2 authored FIRST, V1 (its background) authored SECOND -- deliberate,
    # see this script's module docstring.
    visual_plan = VisualPlan(
        script_plan_id=script_plan.id,
        voice_plan_id=voice_plan.id,
        beats=[
            VisualBeat(
                beat_id="V2",
                script_line_ids=["L001"],
                narrative_node="Q0",
                visual_level="L1_ESTABLISH",
                visual_function="STORY",
                media_type="TI_STATE",
                complexity="C1",
                concept="Tí reacts in front of the bridge scene",
                primary_focus="Tí's reaction",
                ti_state=BACKGROUND_TI_STATE,
            ),
            VisualBeat(
                beat_id="V1",
                script_line_ids=["L001"],
                narrative_node="Q0",
                visual_level="L1_ESTABLISH",
                visual_function="STORY",
                media_type="GENERATED_STILL",
                complexity="C1",
                concept="A wide shot of the Tacoma Narrows Bridge",
                primary_focus="The bridge deck",
            ),
        ],
    )
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE
    return project, script_plan, voice_plan, visual_plan


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    prod_engine = init_database(DEFAULT_DB_PATH)
    try:
        active_set = get_active_ti_asset_set(prod_engine)
    except TiAssetSetNotFoundError as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return 1

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if EVAL_DB_PATH.is_file():
        EVAL_DB_PATH.unlink()  # fresh, disposable DB every run
    eval_engine = init_database(EVAL_DB_PATH)
    save_ti_asset_set(eval_engine, active_set)  # metadata only; PNG bytes stay on shared disk

    ti_compositor = TiCompositor(
        SqliteTiAssetRetriever(eval_engine, TiAssetFileStore(DEFAULT_TI_ASSET_ROOT))
    )
    project, *_ = _build_two_beat_project_at_mvp_complete(eval_engine)

    # Exactly one queued response -- for V1. If V2 ever triggered a second
    # provider call (a fallback bug), FakeVisualProvider would raise
    # "exhausted" instead of the renderer succeeding.
    provider = FakeVisualProvider([_fake_still_response()])
    store = VisualFileStore(EVAL_DIR / "visuals")
    renderer = VisualRenderer(
        eval_engine, provider, VisualSettings(provider="none"), store, ti_compositor=ti_compositor
    )

    renderer_input = VisualRendererInput(
        project_id=project.project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    print(f"Active TiAssetSet: {active_set.id} version {active_set.version!r}")
    print(f"Evaluation project: {project.project_id}")
    print("Authored VisualPlan beat order: V2 (TI_STATE, depends on V1), V1 (GENERATED_STILL)")
    print("No background_path supplied anywhere -- V2 resolves V1's own rendered output.")
    print()

    result = renderer.run(renderer_input)

    render_job_order = [asset.beat_id for asset in result.manifest.assets]
    v1_asset = next(a for a in result.manifest.assets if a.beat_id == "V1")
    v2_asset = next(a for a in result.manifest.assets if a.beat_id == "V2")
    v2_output_path = store.root / v2_asset.file_path

    print("Rendering order actually used (by manifest.assets order):")
    print(f"   {render_job_order}")
    print(f"   V1 rendered before V2 despite being authored second: "
          f"{render_job_order.index('V1') < render_job_order.index('V2')}")
    print()
    print("V1 (GENERATED_STILL, background source):")
    print(f"   file:           {store.root / v1_asset.file_path}")
    print(f"   dimensions:     {v1_asset.width}x{v1_asset.height}")
    print()
    print("V2 (TI_STATE COMPOSITE, background_beat_id='V1'):")
    print(f"   resolved_ti_state: {v2_asset.resolved_ti_state.value}")
    print(f"   output file:       {v2_output_path}")
    print(f"   dimensions:        {v2_asset.width}x{v2_asset.height}")
    print(f"   is PNG:            {v2_asset.file_path.endswith('.png')}")
    print()
    print(f"Total provider calls this run: {result.provider_call_count} (expected 1, only for V1)")
    print(f"Both V1 and V2 present in manifest.assets: "
          f"{{'V1', 'V2'}} == {{a.beat_id for a in result.manifest.assets}}")
    print()
    print(f"Human-inspectable composite output written to: {v2_output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
