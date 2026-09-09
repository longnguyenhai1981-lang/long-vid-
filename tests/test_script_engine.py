from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.config.loader import load_global_config
from app.engines.errors import EngineStateError
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.engine import ScriptEngine
from app.engines.script.errors import (
    MissingIdeaArtifactError,
    MissingNarrativePlanArtifactError,
    MissingPackagingPrototypeArtifactError,
    MissingResearchPackageArtifactError,
    ScriptBusinessValidationError,
)
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE, ScriptEngineInput
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import GateEvaluation, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptPlan
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.errors import ArtifactNotFoundError
from app.storage.module_runs import list_module_runs_for_project
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state


def _global_config():
    return load_global_config()


def _llm_settings(max_structured_retries: int = 2) -> LLMSettings:
    return LLMSettings(
        provider="fake", default_model="fake-model", max_structured_retries=max_structured_retries
    )


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _valid_idea_candidate(**overrides) -> IdeaCandidate:
    kwargs = dict(
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
    kwargs.update(overrides)
    return IdeaCandidate(**kwargs)


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


def _valid_packaging_prototype(risk: str = "LOW") -> PackagingPrototype:
    return PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading=risk,
    )


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01 - Tacoma Narrows", created_at=created, updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


def _create_project_in_script(engine, idea=None):
    idea = idea or _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)

    research_r0 = _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = _valid_feasibility_report()
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    research_package = _valid_research_package(idea.central_question)
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)

    narrative_plan = _valid_narrative_plan(idea.central_question)
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, narrative_plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", narrative_plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)

    packaging = _valid_packaging_prototype()
    save_artifact(engine, project_id, PACKAGING_PROTOTYPE_ARTIFACT_TYPE, packaging)
    update_artifact_reference(engine, project_id, "packaging_prototype_id", packaging.id)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    return project_id, idea, research_package, narrative_plan, packaging


def _line_dict(line_id="L001", claim_ids=None, function="INFORM"):
    return {
        "line_id": line_id,
        "text": "Tí kể một chuyện thú vị cho bạn nghe.",
        "function": function,
        "claim_ids": claim_ids if claim_ids is not None else ["C001"],
    }


def _beat_dict(beat_id="B001", narrative_node="Q0", lines=None):
    return {
        "beat_id": beat_id,
        "narrative_node": narrative_node,
        "narrative_function": "INFORM",
        "lines": lines if lines is not None else [_line_dict()],
    }


def _script_json(beats=None, duration: int = 480) -> str:
    return json.dumps(
        {
            "estimated_duration_seconds": duration,
            "beats": beats if beats is not None else [_beat_dict()],
            "claim_coverage": ["C001"],
            "unmapped_claims": [],
            "qa_status": "PASS",
        }
    )


# ---------------------------------------------------------------------------
# Section 44: success
# ---------------------------------------------------------------------------


def test_script_success(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    provider = FakeLLMProvider([_response(_script_json())])

    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    result = script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 1
    assert isinstance(result.script, ScriptPlan)

    stored = get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
    assert stored == result.script

    project = get_project(engine, project_id)
    assert project.script_plan_id == result.script.id
    assert project.state == ProjectState.SCRIPT_VERIFICATION

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.SUCCESS
    assert runs[0].output_id == str(result.script.id)
    assert result.business_correction_used is False


# ---------------------------------------------------------------------------
# Section 45: structured retry
# ---------------------------------------------------------------------------


def test_script_structured_retry_then_success(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    invalid_json = '{"estimated_duration_seconds": 480}'
    provider = FakeLLMProvider([_response(invalid_json), _response(_script_json())])

    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    result = script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.generation_attempts == 2
    assert result.business_correction_used is False


# ---------------------------------------------------------------------------
# Section 46: business correction
# ---------------------------------------------------------------------------


def test_script_business_correction_then_success(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    bad = _response(_script_json(beats=[_beat_dict("B001", "Q999")]))
    good = _response(_script_json())
    provider = FakeLLMProvider([bad, good])

    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    result = script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert "unknown narrative_node" in provider.received_requests[1].user_prompt

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT_VERIFICATION


# ---------------------------------------------------------------------------
# Section 47: business correction exhaustion
# ---------------------------------------------------------------------------


def test_script_business_correction_exhaustion(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    bad = _response(_script_json(beats=[_beat_dict("B001", "Q999")]))
    still_bad = _response(_script_json(beats=[_beat_dict("B001", "Q888")]))
    provider = FakeLLMProvider([bad, still_bad])

    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(ScriptBusinessValidationError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 2

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT
    assert project.script_plan_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)


# ---------------------------------------------------------------------------
# Section 57: invalid start state
# ---------------------------------------------------------------------------


def test_script_invalid_start_state(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)
    provider = FakeLLMProvider([])

    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(EngineStateError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 58: missing upstream artifact
# ---------------------------------------------------------------------------


def test_script_missing_idea_candidate(engine):
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    provider = FakeLLMProvider([])
    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingIdeaArtifactError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_script_missing_research_package(engine):
    idea = _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    provider = FakeLLMProvider([])
    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingResearchPackageArtifactError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_script_missing_narrative_plan(engine):
    idea = _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    research_package = _valid_research_package(idea.central_question)
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    provider = FakeLLMProvider([])
    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingNarrativePlanArtifactError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_script_missing_packaging_prototype(engine):
    idea = _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    research_package = _valid_research_package(idea.central_question)
    save_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, research_package)
    update_artifact_reference(engine, project_id, "research_r1_id", research_package.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)

    narrative_plan = _valid_narrative_plan(idea.central_question)
    save_artifact(engine, project_id, NARRATIVE_PLAN_ARTIFACT_TYPE, narrative_plan)
    update_artifact_reference(engine, project_id, "narrative_plan_id", narrative_plan.id)
    update_project_state(engine, project_id, ProjectState.NARRATIVE_REVIEW)
    update_project_state(engine, project_id, ProjectState.PACKAGING_P0)
    update_project_state(engine, project_id, ProjectState.SCRIPT)

    provider = FakeLLMProvider([])
    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingPackagingPrototypeArtifactError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 59: LLM failure
# ---------------------------------------------------------------------------


def test_script_llm_failure_marks_module_run_failed(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])

    script_engine = ScriptEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(LLMProviderError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT
    assert project.script_plan_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)


# ---------------------------------------------------------------------------
# Section 60: structured exhaustion
# ---------------------------------------------------------------------------


def test_script_structured_exhaustion(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    invalid_json = '{"estimated_duration_seconds": 480}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(invalid_json), _response(invalid_json)]
    )

    script_engine = ScriptEngine(
        engine, provider, _global_config(), _llm_settings(max_structured_retries=2)
    )
    with pytest.raises(StructuredOutputExhaustedError):
        script_engine.run(ScriptEngineInput(project_id=project_id))

    assert provider.call_count == 3

    project = get_project(engine, project_id)
    assert project.state == ProjectState.SCRIPT
    assert project.script_plan_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan)
