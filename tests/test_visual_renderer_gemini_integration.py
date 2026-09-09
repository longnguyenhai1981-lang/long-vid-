from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from google.genai import errors as genai_errors

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
from app.models.common import (
    GateEvaluation,
    GateStatus,
    ModuleRunStatus,
    PrimaryPayoff,
    ProjectState,
)
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.models.diagram import DiagramCanvas, DiagramLine, DiagramPoint, DiagramSpec
from app.models.visual import VisualBeat, VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import list_module_runs_for_project, save_module_run
from app.storage.projects import (
    create_project,
    get_project,
    update_artifact_reference,
    update_project_state,
)
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.router import MediaTypeVisualProvider
from app.visual.storage import VisualFileStore
from app.visual.providers.gemini import (
    GEMINI_IMAGE_PROVIDER_NAME,
    GeminiImageConfig,
    GeminiImageProvider,
)


# ---------------------------------------------------------------------------
# Fake Gemini client (mirrors tests/test_gemini_image_provider.py's; test-local)
# Phase 20.1: mocks generate_content (not the deprecated generate_images).
# ---------------------------------------------------------------------------


def _fake_image_response(image_bytes=b"fake-png-bytes", mime_type="image/png", response_id="req-1"):
    inline_data = SimpleNamespace(data=image_bytes, mime_type=mime_type)
    part = SimpleNamespace(inline_data=inline_data, text=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate], response_id=response_id, prompt_feedback=None)


class _FakeModels:
    def __init__(self, results):
        # results: list of SimpleNamespace (success) or Exception, consumed in order.
        self._results = list(results)
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        call_number = len(self.calls)
        self.calls.append(kwargs)
        result = self._results[call_number]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeClient:
    def __init__(self, results):
        self.models = _FakeModels(results)


# ---------------------------------------------------------------------------
# Fixture builders (mirroring tests/test_visual_renderer.py)
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


def _valid_script_plan(line_ids=("L001", "L002")) -> ScriptPlan:
    lines = [
        ScriptLine(line_id=lid, text=f"Đây là câu {lid}.", function="INFORM") for lid in line_ids
    ]
    return ScriptPlan(
        estimated_duration_seconds=60,
        beats=[ScriptBeat(beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=lines)],
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


def _save_voice_plan(engine, project_id, script_plan_id) -> VoicePlan:
    chunk = VoiceChunk(
        chunk_id="C001",
        line_ids=["L001", "L002"],
        voice_state="EXCITED",
        pace="FAST",
        energy="HIGH",
        take_count=1,
        music_state="BED",
    )
    voice_plan = VoicePlan(script_plan_id=script_plan_id, chunks=[chunk])
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    return voice_plan


def _diagram_spec() -> DiagramSpec:
    return DiagramSpec(
        canvas=DiagramCanvas(width=100, height=100),
        elements=[DiagramLine(start=DiagramPoint(x=0.1, y=0.1), end=DiagramPoint(x=0.9, y=0.9))],
    )


def _visual_beat(beat_id, media_type="GENERATED_STILL", **overrides) -> VisualBeat:
    fields = dict(
        beat_id=beat_id,
        script_line_ids=["L001"],
        narrative_node="Q0",
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type=media_type,
        complexity="C1",
        concept=f"Concept for {beat_id}",
        primary_focus=f"Focus for {beat_id}",
    )
    if media_type == "DIAGRAM" and "diagram_spec" not in overrides:
        fields["diagram_spec"] = _diagram_spec()
    fields.update(overrides)
    return VisualBeat(**fields)


def _save_visual_plan(engine, project_id, script_plan_id, voice_plan_id, beats) -> VisualPlan:
    visual_plan = VisualPlan(script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, beats=beats)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    return visual_plan


def _ready_project(engine, beats=None):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _save_voice_plan(engine, project_id, script_plan.id)
    visual_plan = _save_visual_plan(
        engine,
        project_id,
        script_plan.id,
        voice_plan.id,
        beats or [_visual_beat("V001", media_type="GENERATED_STILL")],
    )
    return project_id, script_plan, voice_plan, visual_plan


# ---------------------------------------------------------------------------
# Section 50: VisualRenderer + GeminiImageProvider integration
# ---------------------------------------------------------------------------


def test_renderer_uses_gemini_image_provider_with_no_gemini_specific_knowledge(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _ready_project(engine)

    client = _FakeClient([_fake_image_response()])
    gemini_provider = GeminiImageProvider(GeminiImageConfig(), client=client)
    store = VisualFileStore(tmp_path / "visuals")

    # VisualRenderer is constructed exactly as it would be for
    # FakeVisualProvider -- no Gemini-specific parameter, subclass, or
    # branch anywhere in app/renderers/visual/renderer.py.
    settings = VisualSettings(provider=GEMINI_IMAGE_PROVIDER_NAME)
    renderer = VisualRenderer(engine, gemini_provider, settings, store)
    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert result.manifest.provider == GEMINI_IMAGE_PROVIDER_NAME
    assert len(client.models.calls) == 1

    asset = result.manifest.assets[0]
    written_path = store.root / asset.file_path
    assert written_path.exists()
    assert written_path.read_bytes() == b"fake-png-bytes"

    stored = get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)
    assert stored.provider == GEMINI_IMAGE_PROVIDER_NAME

    render_runs = [
        run
        for run in list_module_runs_for_project(engine, project_id)
        if run.module == "visual_renderer"
    ]
    assert render_runs[0].status == ModuleRunStatus.SUCCESS

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE


# ---------------------------------------------------------------------------
# Section 51: renderer retry with a Gemini error
# ---------------------------------------------------------------------------


def test_renderer_retries_once_on_gemini_provider_error_then_succeeds(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _ready_project(engine)

    transient_error = genai_errors.ServerError(
        code=503, response_json={"error": {"message": "overloaded", "status": "UNAVAILABLE"}}
    )
    client = _FakeClient([transient_error, _fake_image_response()])
    gemini_provider = GeminiImageProvider(GeminiImageConfig(), client=client)
    store = VisualFileStore(tmp_path / "visuals")

    settings = VisualSettings(provider=GEMINI_IMAGE_PROVIDER_NAME, max_provider_retries=1)
    renderer = VisualRenderer(engine, gemini_provider, settings, store)
    result = renderer.run(VisualRendererInput(project_id=project_id))

    # Exactly 2 -- one failure plus one success. Not 3 or 4: proves the
    # Gemini image adapter added no retry of its own on top of
    # VisualRenderer's.
    assert len(client.models.calls) == 2
    assert result.provider_call_count == 2
    assert result.rendered_asset_count == 1


# ---------------------------------------------------------------------------
# Section 52 (Phase 19) / Phase 25 update: production routing --
# GENERATED_STILL to Gemini, DIAGRAM bypasses the provider/router entirely;
# FakeVisualProvider renderer tests (tests/test_visual_renderer.py) remain
# fully unchanged.
# ---------------------------------------------------------------------------


def test_production_router_sends_generated_still_to_gemini_diagram_bypasses_provider(engine, tmp_path):
    """Pre-Phase-25, a DIAGRAM beat reached this same router (which has no
    DIAGRAM entry in production) and failed as VisualProviderUnavailableError.
    Phase 25 removes DIAGRAM from provider routing entirely -- it is
    rendered locally by DiagramRenderer instead, so it succeeds here even
    though the router still has no DIAGRAM entry configured; that entry
    simply never gets consulted."""
    beats = [
        _visual_beat("V001", media_type="GENERATED_STILL"),
        _visual_beat("V002", media_type="DIAGRAM"),
    ]
    project_id, script_plan, voice_plan, visual_plan = _ready_project(engine, beats=beats)

    client = _FakeClient([_fake_image_response()])
    gemini_provider = GeminiImageProvider(GeminiImageConfig(), client=client)
    router = MediaTypeVisualProvider({"GENERATED_STILL": gemini_provider})
    store = VisualFileStore(tmp_path / "visuals")

    settings = VisualSettings(provider=GEMINI_IMAGE_PROVIDER_NAME)
    renderer = VisualRenderer(engine, router, settings, store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    # V001 (GENERATED_STILL) reached Gemini exactly once; V002 (DIAGRAM)
    # never reaches the router/provider at all.
    assert len(client.models.calls) == 1
    assert result.provider_call_count == 1
    assert result.rendered_asset_count == 2
    assert {asset.beat_id for asset in result.manifest.assets} == {"V001", "V002"}


def test_fake_visual_provider_renderer_tests_are_unaffected_by_gemini_integration(engine, tmp_path):
    """A FakeVisualProvider-driven run behaves identically to Phase 19 --
    this file's Gemini-specific fixtures/imports change nothing about the
    existing renderer/fake-provider contract."""
    project_id, script_plan, voice_plan, visual_plan = _ready_project(engine)

    provider = FakeVisualProvider([_response()])
    store = VisualFileStore(tmp_path / "visuals")

    settings = VisualSettings(provider="fake-visual")
    renderer = VisualRenderer(engine, provider, settings, store)
    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert result.manifest.provider == "fake-visual"
    assert provider.call_count == 1


def _response(**overrides) -> VisualRenderResponse:
    fields = dict(asset_bytes=b"fake-png-bytes", provider="fake-visual", output_format="PNG")
    fields.update(overrides)
    return VisualRenderResponse(**fields)


# ---------------------------------------------------------------------------
# Section 55: VisualRenderer stays vendor-independent -- zero Gemini-specific
# code was added to app/renderers/visual/renderer.py this phase.
# ---------------------------------------------------------------------------


def test_visual_renderer_source_contains_no_gemini_specific_reference():
    from pathlib import Path

    renderer_path = (
        Path(__file__).resolve().parent.parent / "app" / "renderers" / "visual" / "renderer.py"
    )
    source = renderer_path.read_text(encoding="utf-8")
    lowered = source.lower()
    assert "gemini" not in lowered
    assert "google.genai" not in lowered
    assert "imagen" not in lowered
