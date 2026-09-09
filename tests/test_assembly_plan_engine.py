from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.config.loader import load_global_config
from app.engines.errors import EngineStateError
from app.engines.assembly_plan.engine import AssemblyPlanningEngine
from app.engines.assembly_plan.errors import (
    AssemblyPlanBusinessValidationError,
    MissingScriptPlanArtifactError,
    MissingVisualPlanArtifactError,
    MissingVoicePlanArtifactError,
    StaleVisualPlanError,
    StaleVoicePlanError,
)
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlanningInput
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
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
from app.models.assembly import AssemblyPlan
from app.models.common import GateEvaluation, GateStatus, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
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


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]):
    """Phase 12's review-layer freshness checks read the most recent
    successful ModuleRun for the generating engine. This fixture builds
    PackagingPrototype/ScriptVerificationReport directly (bypassing those
    real engines, to keep this chain focused on Assembly Planning), so a
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

    return project_id, script_plan


def _assembly_segment_dict(segment_id, script_line_ids, visual_beat_id, start, end, voice_chunk_ids=None, music_state="BED"):
    return {
        "segment_id": segment_id,
        "script_line_ids": script_line_ids,
        "voice_chunk_ids": voice_chunk_ids or [],
        "visual_beat_id": visual_beat_id,
        "start_seconds": start,
        "end_seconds": end,
        "music_state": music_state,
        "transition_in": "CUT",
        "transition_out": "CUT",
    }


def _assembly_plan_json(script_plan_id, voice_plan_id, visual_plan_id, segments=None, total_duration=480.0) -> str:
    return json.dumps(
        {
            "script_plan_id": str(script_plan_id),
            "voice_plan_id": str(voice_plan_id),
            "visual_plan_id": str(visual_plan_id),
            "estimated_total_duration_seconds": total_duration,
            "segments": segments
            if segments is not None
            else [
                _assembly_segment_dict("S001", ["L001", "L002"], "VB001", 0.0, 240.0),
                _assembly_segment_dict("S002", ["L003", "L004"], "VB002", 240.0, 480.0, music_state="LIFT"),
            ],
        }
    )


def _setup_project_with_voice_and_visual(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _valid_visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    return project_id, script_plan, voice_plan, visual_plan


# ---------------------------------------------------------------------------
# Section 47: success
# ---------------------------------------------------------------------------


def test_assembly_plan_success(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    provider = FakeLLMProvider([_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id))])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    result = assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert provider.call_count == 1
    assert isinstance(result.assembly_plan, AssemblyPlan)
    assert result.business_correction_used is False
    assert [seg.voice_chunk_ids for seg in result.assembly_plan.segments] == [["VC001"], ["VC002"]]

    stored = get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
    assert stored == result.assembly_plan

    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    assembly_runs = [run for run in runs if run.module == "assembly_plan_engine"]
    assert len(assembly_runs) == 1
    assert assembly_runs[0].status == ModuleRunStatus.SUCCESS
    assert assembly_runs[0].output_id == str(result.assembly_plan.id)
    assert assembly_runs[0].input_ids == [str(script_plan.id), str(voice_plan.id), str(visual_plan.id)]


# ---------------------------------------------------------------------------
# Section 51/52/53: upstream immutability
# ---------------------------------------------------------------------------


def test_assembly_plan_never_mutates_upstream_artifacts(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    script_before = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    voice_before = get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
    visual_before = get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)

    provider = FakeLLMProvider([_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id))])
    AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings()).run(
        AssemblyPlanningInput(project_id=project_id)
    )

    assert get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan) == script_before
    assert get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan) == voice_before
    assert get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan) == visual_before


# ---------------------------------------------------------------------------
# Section 48: stale VoicePlan
# ---------------------------------------------------------------------------


def test_stale_voice_plan_blocks_before_any_llm_call_or_module_run(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    stale_voice_plan = _valid_voice_plan(uuid4())  # references a different ScriptPlan
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, stale_voice_plan)
    visual_plan = _valid_visual_plan(script_plan.id, stale_voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleVoicePlanError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "assembly_plan_engine" for run in runs)
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)


# ---------------------------------------------------------------------------
# Section 49: stale VisualPlan by script
# ---------------------------------------------------------------------------


def test_stale_visual_plan_by_script_blocks_before_any_llm_call_or_module_run(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    stale_visual_plan = _valid_visual_plan(uuid4(), voice_plan.id)  # different ScriptPlan
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, stale_visual_plan)

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleVisualPlanError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "assembly_plan_engine" for run in runs)


# ---------------------------------------------------------------------------
# Section 50: stale VisualPlan by voice
# ---------------------------------------------------------------------------


def test_stale_visual_plan_by_voice_blocks_before_any_llm_call_or_module_run(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    stale_visual_plan = _valid_visual_plan(script_plan.id, uuid4())  # different VoicePlan
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, stale_visual_plan)

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(StaleVisualPlanError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "assembly_plan_engine" for run in runs)


# ---------------------------------------------------------------------------
# Section 71: AssemblyPlan freshness detectability
# ---------------------------------------------------------------------------


def test_assembly_plan_records_visual_plan_id_for_freshness_detection(engine):
    project_id, script_plan, voice_plan, visual_plan_a = _setup_project_with_voice_and_visual(engine)

    provider = FakeLLMProvider([_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan_a.id))])
    result = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings()).run(
        AssemblyPlanningInput(project_id=project_id)
    )
    assert result.assembly_plan.visual_plan_id == visual_plan_a.id

    # VisualPlan regenerates (still against the same Script/VoicePlan, so it
    # isn't "stale" by the freshness gate) -- but the stored AssemblyPlan
    # was built from the OLD VisualPlan.
    visual_plan_b = _valid_visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan_b)

    stored_assembly_plan = get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
    current_visual_plan = get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan)

    assert stored_assembly_plan.visual_plan_id != current_visual_plan.id

    runs = list_module_runs_for_project(engine, project_id)
    assembly_run = next(run for run in runs if run.module == "assembly_plan_engine")
    assert assembly_run.input_ids[2] == str(visual_plan_a.id)
    assert assembly_run.input_ids[2] != str(current_visual_plan.id)


# ---------------------------------------------------------------------------
# Section 66: structured retry
# ---------------------------------------------------------------------------


def test_assembly_plan_structured_retry_then_success(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    invalid_json = '{"segments": []}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id))]
    )
    result = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings()).run(
        AssemblyPlanningInput(project_id=project_id)
    )

    assert provider.call_count == 2
    assert result.generation_attempts == 2
    assert result.business_correction_used is False


# ---------------------------------------------------------------------------
# Section 67: business correction
# ---------------------------------------------------------------------------


def test_assembly_plan_business_correction_then_success(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    # Timeline gap between segments -- a genuinely LLM-owned timing problem,
    # not a deterministic id the engine would silently fix.
    bad = _response(
        _assembly_plan_json(
            script_plan.id, voice_plan.id, visual_plan.id,
            segments=[
                _assembly_segment_dict("S001", ["L001", "L002"], "VB001", 0.0, 240.0),
                _assembly_segment_dict("S002", ["L003", "L004"], "VB002", 250.0, 480.0, music_state="LIFT"),
            ],
        )
    )
    fixed = _response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id))
    provider = FakeLLMProvider([bad, fixed])

    result = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings()).run(
        AssemblyPlanningInput(project_id=project_id)
    )

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert "contiguous" in provider.received_requests[1].user_prompt
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE


# ---------------------------------------------------------------------------
# Section 68: business correction exhaustion
# ---------------------------------------------------------------------------


def test_assembly_plan_business_correction_exhaustion(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    def _gapped(gap_start):
        return _response(
            _assembly_plan_json(
                script_plan.id, voice_plan.id, visual_plan.id,
                segments=[
                    _assembly_segment_dict("S001", ["L001", "L002"], "VB001", 0.0, 240.0),
                    _assembly_segment_dict("S002", ["L003", "L004"], "VB002", gap_start, 480.0, music_state="LIFT"),
                ],
            )
        )

    provider = FakeLLMProvider([_gapped(250.0), _gapped(260.0)])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(AssemblyPlanBusinessValidationError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert provider.call_count == 2
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    assembly_runs = [run for run in runs if run.module == "assembly_plan_engine"]
    assert len(assembly_runs) == 1
    assert assembly_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)


# ---------------------------------------------------------------------------
# Invalid start state / missing artifacts
# ---------------------------------------------------------------------------


def test_assembly_plan_invalid_start_state(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.state = ProjectState.SCRIPT_REVIEW.value

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(EngineStateError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))
    assert provider.call_count == 0


def test_assembly_plan_missing_script_plan(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.script_plan_id = None

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingScriptPlanArtifactError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))
    assert provider.call_count == 0


def test_assembly_plan_missing_voice_plan(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    visual_plan = _valid_visual_plan(script_plan.id, uuid4())
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    # No VoicePlan artifact saved at all.

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingVoicePlanArtifactError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))
    assert provider.call_count == 0


def test_assembly_plan_missing_visual_plan(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    # No VisualPlan artifact saved at all.

    provider = FakeLLMProvider([])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingVisualPlanArtifactError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# Section 69: LLM failure
# ---------------------------------------------------------------------------


def test_assembly_plan_llm_failure_marks_module_run_failed(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])
    assembly_engine = AssemblyPlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(LLMProviderError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    assembly_runs = [run for run in runs if run.module == "assembly_plan_engine"]
    assert len(assembly_runs) == 1
    assert assembly_runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in assembly_runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)


# ---------------------------------------------------------------------------
# Section 70: structured exhaustion
# ---------------------------------------------------------------------------


def test_assembly_plan_structured_exhaustion(engine):
    project_id, script_plan, voice_plan, visual_plan = _setup_project_with_voice_and_visual(engine)

    invalid_json = '{"segments": []}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(invalid_json), _response(invalid_json)]
    )
    assembly_engine = AssemblyPlanningEngine(
        engine, provider, _global_config(), _llm_settings(max_structured_retries=2)
    )
    with pytest.raises(StructuredOutputExhaustedError):
        assembly_engine.run(AssemblyPlanningInput(project_id=project_id))

    assert provider.call_count == 3
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    assembly_runs = [run for run in runs if run.module == "assembly_plan_engine"]
    assert len(assembly_runs) == 1
    assert assembly_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan)
