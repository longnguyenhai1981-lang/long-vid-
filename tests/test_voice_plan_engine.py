from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.config.loader import load_global_config
from app.engines.errors import EngineStateError
from app.engines.feasibility.engine import FeasibilityEngine
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityEngineInput
from app.engines.idea.engine import IdeaEngine
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaEngineInput
from app.engines.narrative.engine import NarrativeEngine
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE, NarrativeEngineInput
from app.engines.packaging_p0.engine import PackagingP0Engine
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE, PackagingP0Input
from app.engines.research_r0.engine import R0ResearchEngine
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE, R0ResearchInput
from app.engines.research_r1.engine import R1ResearchEngine
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE, R1ResearchInput
from app.engines.script.engine import ScriptEngine
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE, ScriptEngineInput
from app.engines.script_verification.engine import ScriptVerificationEngine
from app.engines.script_verification.models import (
    SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE,
    ScriptVerificationInput,
)
from app.engines.voice_plan.engine import VoicePlanningEngine
from app.engines.voice_plan.errors import (
    MissingScriptPlanArtifactError,
    VoicePlanBusinessValidationError,
)
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE, VoicePlanningInput
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import GateEvaluation, GateStatus, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.models.voice import VoicePlan
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.models.module_run import ModuleRun
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


def _valid_script_plan(line_ids=("L001", "L002", "L003", "L004")) -> ScriptPlan:
    lines = [
        ScriptLine(line_id=lid, text=f"Đây là câu {lid}.", function="INFORM") for lid in line_ids
    ]
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=lines[:2]),
            ScriptBeat(beat_id="B002", narrative_node="Q0", narrative_function="REVEAL", lines=lines[2:]),
        ]
        if len(lines) >= 2
        else [ScriptBeat(beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=lines)],
        qa_status="PASS",
    )


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]):
    """Phase 12's review-layer freshness checks (app/review/service.py) read
    the most recent successful ModuleRun for the generating engine to prove
    an artifact was built from the project's *current* upstream reference.
    This fixture builds PackagingPrototype/ScriptVerificationReport directly
    (bypassing the real engines, to keep this chain focused on Voice
    Planning), so a plausible ModuleRun is added here to match what a real
    run would have left behind -- only the position(s) the freshness checks
    actually read need to be real ids."""
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
    assert get_project(engine, project_id).state == ProjectState.R0_RESEARCH

    research_r0 = _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = _valid_feasibility_report()
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    decide_feasibility(engine, project_id, GateStatus.PASS)
    assert get_project(engine, project_id).state == ProjectState.R1_RESEARCH

    research_package = _valid_research_package(idea.central_question)
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)

    narrative_plan = _valid_narrative_plan(idea.central_question)
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, narrative_plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", narrative_plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    approve_narrative(engine, project_id)
    assert get_project(engine, project_id).state == ProjectState.PACKAGING_P0

    packaging = _valid_packaging_prototype()
    save_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, packaging)
    update_artifact_reference(engine, project_id, "packaging_prototype_id", packaging.id)
    _record_module_run_success(
        engine, project_id, "packaging_p0_engine", [str(idea.id), str(research_package.id), str(narrative_plan.id)]
    )
    approve_packaging_p0(engine, project_id)
    assert get_project(engine, project_id).state == ProjectState.SCRIPT

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
    assert get_project(engine, project_id).state == ProjectState.SCRIPT_REVIEW

    approve_final_script(engine, project_id)
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    return project_id, script_plan


def _voice_chunk_dict(chunk_id="C001", line_ids=None, take_count=1, music_state="BED", sfx=None):
    return {
        "chunk_id": chunk_id,
        "line_ids": line_ids if line_ids is not None else ["L001"],
        "voice_state": "NEUTRAL",
        "pace": "NORMAL",
        "energy": "MEDIUM",
        "take_count": take_count,
        "music_state": music_state,
        "sfx_opportunity": sfx,
    }


def _voice_plan_json(script_plan_id, chunks=None) -> str:
    return json.dumps(
        {
            "script_plan_id": str(script_plan_id),
            "chunks": chunks
            if chunks is not None
            else [
                _voice_chunk_dict("C001", ["L001", "L002"]),
                _voice_chunk_dict("C002", ["L003", "L004"], take_count=2, music_state="LIFT"),
            ],
        }
    )


# ---------------------------------------------------------------------------
# Section 39: success
# ---------------------------------------------------------------------------


def test_voice_plan_success(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    provider = FakeLLMProvider([_response(_voice_plan_json(script_plan.id))])

    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    result = voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 1
    assert isinstance(result.voice_plan, VoicePlan)
    assert result.business_correction_used is False

    stored = get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
    assert stored == result.voice_plan

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE

    stored_script = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert stored_script == script_plan

    runs = list_module_runs_for_project(engine, project_id)
    voice_runs = [run for run in runs if run.module == "voice_plan_engine"]
    assert len(voice_runs) == 1
    assert voice_runs[0].status == ModuleRunStatus.SUCCESS
    assert voice_runs[0].output_id == str(result.voice_plan.id)
    assert str(script_plan.id) in voice_runs[0].input_ids


# ---------------------------------------------------------------------------
# Section 47: structured retry
# ---------------------------------------------------------------------------


def test_voice_plan_structured_retry_then_success(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    invalid_json = '{"chunks": []}'
    provider = FakeLLMProvider([_response(invalid_json), _response(_voice_plan_json(script_plan.id))])

    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    result = voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.generation_attempts == 2
    assert result.business_correction_used is False


# ---------------------------------------------------------------------------
# Section 48: business correction
# ---------------------------------------------------------------------------


def test_voice_plan_business_correction_then_success(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    missing_one = _response(
        _voice_plan_json(script_plan.id, chunks=[_voice_chunk_dict("C001", ["L001", "L002", "L003"])])
    )
    fixed = _response(_voice_plan_json(script_plan.id))
    provider = FakeLLMProvider([missing_one, fixed])

    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    result = voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert "L004" in provider.received_requests[1].user_prompt

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE


# ---------------------------------------------------------------------------
# Section 49: business correction exhaustion
# ---------------------------------------------------------------------------


def test_voice_plan_business_correction_exhaustion(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    bad = _response(
        _voice_plan_json(script_plan.id, chunks=[_voice_chunk_dict("C001", ["L001", "L002", "L003"])])
    )
    still_bad = _response(
        _voice_plan_json(script_plan.id, chunks=[_voice_chunk_dict("C001", ["L001", "L002"])])
    )
    provider = FakeLLMProvider([bad, still_bad])

    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(VoicePlanBusinessValidationError):
        voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 2

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    voice_runs = [run for run in runs if run.module == "voice_plan_engine"]
    assert len(voice_runs) == 1
    assert voice_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)


# ---------------------------------------------------------------------------
# Section 50: script immutability
# ---------------------------------------------------------------------------


def test_voice_plan_never_mutates_script_plan(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    before = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)

    provider = FakeLLMProvider([_response(_voice_plan_json(script_plan.id))])
    VoicePlanningEngine(engine, provider, _global_config(), _llm_settings()).run(
        VoicePlanningInput(project_id=project_id)
    )

    after = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert after == before


# ---------------------------------------------------------------------------
# Section 51: stale input safety (detectability only, no enforcement)
# ---------------------------------------------------------------------------


def test_voice_plan_records_script_plan_id_for_freshness_detection(engine):
    project_id, script_plan_a = _create_project_at_mvp_complete(engine)
    provider = FakeLLMProvider([_response(_voice_plan_json(script_plan_a.id))])
    result = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings()).run(
        VoicePlanningInput(project_id=project_id)
    )
    assert result.voice_plan.script_plan_id == script_plan_a.id

    # The script changes underneath the stored VoicePlan (e.g. a future
    # rewrite-and-reverify cycle would do this via ScriptEngine).
    script_plan_b = _valid_script_plan(("L001", "L002", "L003", "L004"))
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan_b)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan_b.id)

    stored_voice_plan = get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
    current_project = get_project(engine, project_id)

    # Staleness is detectable from persisted metadata alone -- no downstream
    # enforcement is built in Phase 13, only the ability to detect it.
    assert stored_voice_plan.script_plan_id != current_project.script_plan_id

    runs = list_module_runs_for_project(engine, project_id)
    voice_run = next(run for run in runs if run.module == "voice_plan_engine")
    assert voice_run.input_ids == [str(script_plan_a.id)]
    assert voice_run.input_ids[0] != str(current_project.script_plan_id)


# ---------------------------------------------------------------------------
# Invalid start state / missing artifact
# ---------------------------------------------------------------------------


def test_voice_plan_invalid_start_state(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    # Force the project backward -- not a real recovery route, just to prove
    # the precondition check fires for any non-MVP_COMPLETE state.
    from app.storage.orm import ProjectRow
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.state = ProjectState.SCRIPT_REVIEW.value

    provider = FakeLLMProvider([])
    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(EngineStateError):
        voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 0


def test_voice_plan_missing_script_plan_artifact(engine):
    # A project can only reach MVP_COMPLETE with a script_plan_id already
    # set (approve_final_script requires it) -- simulate the defensive case
    # of a corrupted/cleared reference directly at the storage layer.
    project_id, _ = _create_project_at_mvp_complete(engine)
    from app.storage.orm import ProjectRow
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.script_plan_id = None

    provider = FakeLLMProvider([])
    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingScriptPlanArtifactError):
        voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    assert not any(run.module == "voice_plan_engine" for run in runs)


# ---------------------------------------------------------------------------
# Section 52: LLM failure
# ---------------------------------------------------------------------------


def test_voice_plan_llm_failure_marks_module_run_failed(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])

    voice_engine = VoicePlanningEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(LLMProviderError):
        voice_engine.run(VoicePlanningInput(project_id=project_id))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    voice_runs = [run for run in runs if run.module == "voice_plan_engine"]
    assert len(voice_runs) == 1
    assert voice_runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in voice_runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)


# ---------------------------------------------------------------------------
# Section 53: structured exhaustion
# ---------------------------------------------------------------------------


def test_voice_plan_structured_exhaustion(engine):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    invalid_json = '{"chunks": []}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(invalid_json), _response(invalid_json)]
    )

    voice_engine = VoicePlanningEngine(
        engine, provider, _global_config(), _llm_settings(max_structured_retries=2)
    )
    with pytest.raises(StructuredOutputExhaustedError):
        voice_engine.run(VoicePlanningInput(project_id=project_id))

    assert provider.call_count == 3

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    voice_runs = [run for run in runs if run.module == "voice_plan_engine"]
    assert len(voice_runs) == 1
    assert voice_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan)
