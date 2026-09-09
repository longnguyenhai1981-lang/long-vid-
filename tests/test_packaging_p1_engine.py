from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.config.loader import load_global_config
from app.engines.errors import EngineStateError
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.packaging_p1.engine import PackagingP1Engine
from app.engines.packaging_p1.errors import (
    MissingAssemblyPlanArtifactError,
    MissingNarrativePlanArtifactError,
    MissingPackagingPrototypeArtifactError,
    MissingResearchPackageArtifactError,
    MissingScriptPlanArtifactError,
    MissingVisualPlanArtifactError,
    MissingVoicePlanArtifactError,
    PackagingP1BusinessValidationError,
    StaleAssemblyPlanError,
    StaleVisualPlanError,
    StaleVoicePlanError,
)
from app.engines.packaging_p1.models import PACKAGING_P1_ARTIFACT_TYPE, PackagingP1Input
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.models import SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.common import GateEvaluation, GateStatus, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0, Source
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.models.visual import VisualBeat, VisualPlan
from app.models.voice import VoiceChunk, VoicePlan
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
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state


def _global_config():
    return load_global_config()


def _llm_settings(max_structured_retries: int = 2) -> LLMSettings:
    return LLMSettings(
        provider="fake", default_model="fake-model", max_structured_retries=max_structured_retries
    )


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


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
        idea_id=idea_id, topic_valid=True, credible_sources_available=True,
        story_material_available=True, physics_material_available=True, recommendation="CONTINUE",
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
        simplification_boundary="Do not replace flutter with resonance.",
        claims=[Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")],
        sources=[Source(source_id="S001", title="1940 collapse newsreel", type="video", quality_tier=1, authoritative=True)],
    )


def _valid_narrative_plan(central_question: str) -> NarrativePlan:
    return NarrativePlan(
        central_question=central_question,
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
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


def _valid_packaging_prototype() -> PackagingPrototype:
    return PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading="LOW",
    )


def _valid_script_plan(line_ids=("L001", "L002", "L003", "L004")) -> ScriptPlan:
    lines = [ScriptLine(line_id=lid, text=f"Đây là câu {lid}.", function="INFORM") for lid in line_ids]
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[ScriptBeat(beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=lines)],
        qa_status="PASS",
    )


def _valid_voice_plan(script_plan_id: UUID) -> VoicePlan:
    return VoicePlan(
        script_plan_id=script_plan_id,
        chunks=[
            VoiceChunk(chunk_id="VC001", line_ids=["L001", "L002"], voice_state="NEUTRAL", pace="NORMAL", energy="MEDIUM", take_count=1, music_state="BED"),
            VoiceChunk(chunk_id="VC002", line_ids=["L003", "L004"], voice_state="SERIOUS", pace="SLOW", energy="MEDIUM", take_count=1, music_state="LIFT"),
        ],
    )


def _valid_visual_plan(script_plan_id: UUID, voice_plan_id: UUID) -> VisualPlan:
    return VisualPlan(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id,
        beats=[
            VisualBeat(beat_id="VB001", script_line_ids=["L001", "L002"], narrative_node="Q0", visual_level="L1_ESTABLISH", visual_function="STORY", media_type="ASSET_REUSE", complexity="C0", concept="Establishing shot.", primary_focus="The bridge"),
            VisualBeat(beat_id="VB002", script_line_ids=["L003", "L004"], narrative_node="Q0", visual_level="L3_RELATIONSHIP", visual_function="MECHANISM", media_type="DIAGRAM", complexity="C1", concept="Flutter diagram.", primary_focus="The twisting deck"),
        ],
    )


def _valid_assembly_plan(script_plan_id: UUID, voice_plan_id: UUID, visual_plan_id: UUID) -> AssemblyPlan:
    return AssemblyPlan(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, visual_plan_id=visual_plan_id,
        estimated_total_duration_seconds=480.0,
        segments=[
            AssemblySegment(segment_id="S001", script_line_ids=["L001", "L002"], voice_chunk_ids=["VC001"], visual_beat_id="VB001", start_seconds=0.0, end_seconds=240.0, music_state="BED", transition_in="CUT", transition_out="CUT"),
            AssemblySegment(segment_id="S002", script_line_ids=["L003", "L004"], voice_chunk_ids=["VC002"], visual_beat_id="VB002", start_seconds=240.0, end_seconds=480.0, music_state="LIFT", transition_in="DISSOLVE", transition_out="CUT"),
        ],
    )


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]):
    """Phase 12's review-layer freshness checks read the most recent
    successful ModuleRun for the generating engine. This fixture builds
    PackagingPrototype/ScriptVerificationReport directly (bypassing those
    real engines, to keep this chain focused on Packaging P1), so a
    plausible ModuleRun is added to match what a real run would leave
    behind."""
    now = datetime.now(timezone.utc)
    save_module_run(
        engine,
        ModuleRun(
            project_id=project_id, module=module, module_version="0.1",
            started_at=now, completed_at=now, input_ids=input_ids, status=ModuleRunStatus.SUCCESS,
        ),
    )


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01 - Tacoma Narrows", created_at=created, updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


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
        engine, project_id, "packaging_p0_engine", [str(idea.id), str(research_package.id), str(narrative_plan.id)]
    )
    approve_packaging_p0(engine, project_id)

    script_plan = script_plan or _valid_script_plan()
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
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    return project_id, packaging, research_package, narrative_plan, script_plan


def _final_packaging_plan_json(packaging_prototype_id, script_plan_id, visual_plan_id, assembly_plan_id, **overrides) -> str:
    payload = {
        "packaging_prototype_id": str(packaging_prototype_id),
        "script_plan_id": str(script_plan_id),
        "visual_plan_id": str(visual_plan_id),
        "assembly_plan_id": str(assembly_plan_id),
        "title": "Chiếc cầu tự xé nát chính nó",
        "thumbnail_text": "CHỈ VÌ GIÓ?",
        "thumbnail_concept": "Bridge deck twisting sharply; Tí confused in lower-right.",
        "final_promise": "A sturdy bridge tore itself apart in ordinary wind.",
        "expected_payoff": "The real aerodynamic mechanism, not resonance.",
        "viewer_expectation": "An honest investigation into a real physical mechanism.",
        "rationale": "Sharpens P0's promise now the reveal is locked into the script.",
        "risk_of_misleading": "LOW",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _setup_project_with_full_production_stack(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _valid_visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _valid_assembly_plan(script_plan.id, voice_plan.id, visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)
    return project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan


# ---------------------------------------------------------------------------
# Section 39: success
# ---------------------------------------------------------------------------


def test_packaging_p1_success(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    provider = FakeLLMProvider(
        [_response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id))]
    )
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    result = p1_engine.run(PackagingP1Input(project_id=project_id))

    assert provider.call_count == 1
    assert isinstance(result.final_packaging_plan, FinalPackagingPlan)
    assert result.business_correction_used is False

    stored = get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)
    assert stored == result.final_packaging_plan

    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    p1_runs = [run for run in runs if run.module == "packaging_p1_engine"]
    assert len(p1_runs) == 1
    assert p1_runs[0].status == ModuleRunStatus.SUCCESS
    assert p1_runs[0].output_id == str(result.final_packaging_plan.id)
    assert p1_runs[0].input_ids == [
        str(packaging.id), str(script_plan.id), str(visual_plan.id), str(assembly_plan.id), str(research_package.id),
    ]


# ---------------------------------------------------------------------------
# Section 40: wrong output ids normalized
# ---------------------------------------------------------------------------


def test_packaging_p1_normalizes_wrong_upstream_ids_without_correction(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    provider = FakeLLMProvider(
        [_response(_final_packaging_plan_json(uuid4(), uuid4(), uuid4(), uuid4()))]
    )
    result = PackagingP1Engine(engine, provider, _global_config(), _llm_settings()).run(
        PackagingP1Input(project_id=project_id)
    )

    assert provider.call_count == 1
    assert result.business_correction_used is False
    assert result.final_packaging_plan.packaging_prototype_id == packaging.id
    assert result.final_packaging_plan.script_plan_id == script_plan.id
    assert result.final_packaging_plan.visual_plan_id == visual_plan.id
    assert result.final_packaging_plan.assembly_plan_id == assembly_plan.id


# ---------------------------------------------------------------------------
# Section 41: stale VoicePlan
# ---------------------------------------------------------------------------


def test_stale_voice_plan_blocks_before_any_llm_call_or_module_run(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    stale_voice_plan = _valid_voice_plan(uuid4())  # references a different ScriptPlan
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, stale_voice_plan)
    visual_plan = _valid_visual_plan(script_plan.id, stale_voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _valid_assembly_plan(script_plan.id, stale_voice_plan.id, visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleVoicePlanError):
        p1_engine.run(PackagingP1Input(project_id=project_id))

    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "packaging_p1_engine" for run in runs)
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)


# ---------------------------------------------------------------------------
# Section 42: stale VisualPlan (by script or voice)
# ---------------------------------------------------------------------------


def test_stale_visual_plan_by_script_blocks(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    stale_visual_plan = _valid_visual_plan(uuid4(), voice_plan.id)  # different ScriptPlan
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, stale_visual_plan)
    assembly_plan = _valid_assembly_plan(script_plan.id, voice_plan.id, stale_visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleVisualPlanError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_stale_visual_plan_by_voice_blocks(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    stale_visual_plan = _valid_visual_plan(script_plan.id, uuid4())  # different VoicePlan
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, stale_visual_plan)
    assembly_plan = _valid_assembly_plan(script_plan.id, voice_plan.id, stale_visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleVisualPlanError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# Section 43: stale AssemblyPlan
# ---------------------------------------------------------------------------


def test_stale_assembly_plan_blocks(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )
    stale_assembly_plan = _valid_assembly_plan(uuid4(), voice_plan.id, visual_plan.id)  # different ScriptPlan
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, stale_assembly_plan)

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleAssemblyPlanError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "packaging_p1_engine" for run in runs)


# ---------------------------------------------------------------------------
# Section 44: P0 currentness
# ---------------------------------------------------------------------------


def test_mismatched_packaging_prototype_reference_blocks(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )
    # Simulate a data-integrity anomaly: project still points at the real
    # PackagingPrototype id, but a *different* prototype is what's stored.
    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.packaging_prototype_id = str(uuid4())

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingPackagingPrototypeArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# Section 47: HIGH risk persists
# ---------------------------------------------------------------------------


def test_high_risk_final_packaging_still_persists(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    provider = FakeLLMProvider(
        [
            _response(
                _final_packaging_plan_json(
                    packaging.id, script_plan.id, visual_plan.id, assembly_plan.id, risk_of_misleading="HIGH"
                )
            )
        ]
    )
    result = PackagingP1Engine(engine, provider, _global_config(), _llm_settings()).run(
        PackagingP1Input(project_id=project_id)
    )

    assert result.final_packaging_plan.risk_of_misleading.value == "HIGH"
    stored = get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)
    assert stored.risk_of_misleading.value == "HIGH"
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE


# ---------------------------------------------------------------------------
# Section 48: structured retry
# ---------------------------------------------------------------------------


def test_packaging_p1_structured_retry_then_success(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    invalid_json = '{"title": "x"}'
    provider = FakeLLMProvider(
        [
            _response(invalid_json),
            _response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id)),
        ]
    )
    result = PackagingP1Engine(engine, provider, _global_config(), _llm_settings()).run(
        PackagingP1Input(project_id=project_id)
    )

    assert provider.call_count == 2
    assert result.generation_attempts == 2
    assert result.business_correction_used is False


# ---------------------------------------------------------------------------
# Section 49: business correction
# ---------------------------------------------------------------------------


def test_packaging_p1_business_correction_then_success(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    blank_title = _response(
        _final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id, title="   ")
    )
    fixed = _response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id))
    provider = FakeLLMProvider([blank_title, fixed])

    result = PackagingP1Engine(engine, provider, _global_config(), _llm_settings()).run(
        PackagingP1Input(project_id=project_id)
    )

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert "title cannot be blank" in provider.received_requests[1].user_prompt
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE


# ---------------------------------------------------------------------------
# Section 50: business correction exhaustion
# ---------------------------------------------------------------------------


def test_packaging_p1_business_correction_exhaustion(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    def _blank():
        return _response(
            _final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id, title="   ")
        )

    provider = FakeLLMProvider([_blank(), _blank()])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(PackagingP1BusinessValidationError):
        p1_engine.run(PackagingP1Input(project_id=project_id))

    assert provider.call_count == 2
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    p1_runs = [run for run in runs if run.module == "packaging_p1_engine"]
    assert len(p1_runs) == 1
    assert p1_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)


# ---------------------------------------------------------------------------
# Invalid start state / missing artifacts
# ---------------------------------------------------------------------------


def test_packaging_p1_invalid_start_state(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.state = ProjectState.SCRIPT_REVIEW.value

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(EngineStateError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_packaging_p1_missing_script_plan(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.script_plan_id = None

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingScriptPlanArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_packaging_p1_missing_voice_plan(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    visual_plan = _valid_visual_plan(script_plan.id, uuid4())
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _valid_assembly_plan(script_plan.id, uuid4(), visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)
    # No VoicePlan artifact saved at all.

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingVoicePlanArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_packaging_p1_missing_visual_plan(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    # No VisualPlan artifact saved at all.

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingVisualPlanArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_packaging_p1_missing_assembly_plan(engine):
    project_id, packaging, research_package, narrative_plan, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _valid_visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    # No AssemblyPlan artifact saved at all.

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingAssemblyPlanArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_packaging_p1_missing_narrative_plan(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.narrative_plan_id = None

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingNarrativePlanArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


def test_packaging_p1_missing_research_package(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.research_r1_id = None

    provider = FakeLLMProvider([])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingResearchPackageArtifactError):
        p1_engine.run(PackagingP1Input(project_id=project_id))
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# Section 51: LLM failure
# ---------------------------------------------------------------------------


def test_packaging_p1_llm_failure_marks_module_run_failed(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(LLMProviderError):
        p1_engine.run(PackagingP1Input(project_id=project_id))

    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    p1_runs = [run for run in runs if run.module == "packaging_p1_engine"]
    assert len(p1_runs) == 1
    assert p1_runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in p1_runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)


# ---------------------------------------------------------------------------
# Section 52: structured exhaustion
# ---------------------------------------------------------------------------


def test_packaging_p1_structured_exhaustion(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    invalid_json = '{"title": "x"}'
    provider = FakeLLMProvider([_response(invalid_json), _response(invalid_json), _response(invalid_json)])
    p1_engine = PackagingP1Engine(engine, provider, _global_config(), _llm_settings(max_structured_retries=2))
    with pytest.raises(StructuredOutputExhaustedError):
        p1_engine.run(PackagingP1Input(project_id=project_id))

    assert provider.call_count == 3
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    p1_runs = [run for run in runs if run.module == "packaging_p1_engine"]
    assert len(p1_runs) == 1
    assert p1_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)


# ---------------------------------------------------------------------------
# Section 53: upstream immutability
# ---------------------------------------------------------------------------


def test_packaging_p1_never_mutates_any_upstream_artifact(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )

    packaging_before = get_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype)
    research_before = get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
    narrative_before = get_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan)
    script_before = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    visual_before = get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)
    assembly_before = get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)

    provider = FakeLLMProvider(
        [_response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id))]
    )
    PackagingP1Engine(engine, provider, _global_config(), _llm_settings()).run(
        PackagingP1Input(project_id=project_id)
    )

    assert get_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingPrototype) == packaging_before
    assert get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage) == research_before
    assert get_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativePlan) == narrative_before
    assert get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan) == script_before
    assert get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan) == visual_before
    assert get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan) == assembly_before


# ---------------------------------------------------------------------------
# Section 54: final packaging freshness detectability
# ---------------------------------------------------------------------------


def test_final_packaging_plan_records_assembly_plan_id_for_freshness_detection(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan_a = (
        _setup_project_with_full_production_stack(engine)
    )

    provider = FakeLLMProvider(
        [_response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan_a.id))]
    )
    result = PackagingP1Engine(engine, provider, _global_config(), _llm_settings()).run(
        PackagingP1Input(project_id=project_id)
    )
    assert result.final_packaging_plan.assembly_plan_id == assembly_plan_a.id

    # AssemblyPlan regenerates (still against the same Script/Voice/Visual,
    # so it isn't "stale" by the freshness gate) -- but the stored P1 plan
    # was built from the OLD AssemblyPlan.
    assembly_plan_b = _valid_assembly_plan(script_plan.id, voice_plan.id, visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan_b)

    stored_p1 = get_artifact(engine, project_id, PACKAGING_P1_ARTIFACT_TYPE, FinalPackagingPlan)
    current_assembly_plan = get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)

    assert stored_p1.assembly_plan_id != current_assembly_plan.id

    runs = list_module_runs_for_project(engine, project_id)
    p1_run = next(run for run in runs if run.module == "packaging_p1_engine")
    assert p1_run.input_ids[3] == str(assembly_plan_a.id)
    assert p1_run.input_ids[3] != str(current_assembly_plan.id)
