from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.engines.errors import EngineStateError
from app.engines.feasibility.engine import FeasibilityEngine
from app.engines.feasibility.errors import MissingIdeaArtifactError, MissingResearchArtifactError
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityEngineInput
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.config.loader import load_global_config
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import GateEvaluation, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.feasibility import FeasibilityReport
from app.models.idea import ABT, IdeaCandidate
from app.models.project import Project
from app.models.research import ResearchR0
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


def _valid_research_r0(idea_id: UUID, **overrides) -> ResearchR0:
    kwargs = dict(
        idea_id=idea_id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        initial_findings=["Flutter is a documented bridge failure mode"],
        candidate_sources=["https://real.example/source"],
        major_risks=["Risk of conflating flutter with simple resonance"],
        recommendation="CONTINUE",
    )
    kwargs.update(overrides)
    return ResearchR0(**kwargs)


def _feasibility_json(axis_statuses: dict | None = None, overall: str = "PASS") -> str:
    axis_statuses = axis_statuses or {}

    def axis(name):
        return axis_statuses.get(name, "PASS")

    return json.dumps(
        {
            "status": overall,
            "audience": {"status": axis("audience"), "reason": "General audience can follow"},
            "science": {"status": axis("science"), "reason": "Physics mechanism is central"},
            "narrative": {"status": axis("narrative"), "reason": "Strong tension present"},
            "visual": {"status": axis("visual"), "reason": "2D minimalist friendly"},
            "production": {
                "status": axis("production"),
                "estimated_complexity": "LOW",
                "reason": "Feasible in CapCut within 2 days",
            },
            "likely_reusable_assets": ["Bridge diagram"],
            "likely_expensive_scenes": [],
            "suggested_reframes": [],
        }
    )


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01 - Tacoma Narrows", created_at=created, updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


def _create_project_in_feasibility(engine, idea=None, research=None):
    idea = idea or _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)

    research = research or _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research)
    update_artifact_reference(engine, project_id, "research_r0_id", research.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    return project_id, idea, research


# ---------------------------------------------------------------------------
# Section 30: success (PASS)
# ---------------------------------------------------------------------------


def test_feasibility_success_pass(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    provider = FakeLLMProvider([_response(_feasibility_json())])

    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    result = feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 1
    assert isinstance(result.feasibility, FeasibilityReport)
    assert result.feasibility.status.value == "PASS"

    stored = get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
    assert stored == result.feasibility

    project = get_project(engine, project_id)
    assert project.feasibility_id == result.feasibility.id
    assert project.state == ProjectState.FEASIBILITY  # engine never transitions state

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.SUCCESS
    assert runs[0].output_id == str(result.feasibility.id)


# ---------------------------------------------------------------------------
# Section 31: overall status normalization
# ---------------------------------------------------------------------------


def test_overall_status_case_a_reframe_axis_overrides_llm_pass(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    provider = FakeLLMProvider(
        [_response(_feasibility_json(axis_statuses={"narrative": "REFRAME"}, overall="PASS"))]
    )
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    result = feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))
    assert result.feasibility.status.value == "REFRAME"

    stored = get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
    assert stored.status.value == "REFRAME"


def test_overall_status_case_b_reject_axis_overrides_llm_pass(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    provider = FakeLLMProvider(
        [_response(_feasibility_json(axis_statuses={"science": "REJECT"}, overall="PASS"))]
    )
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    result = feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))
    assert result.feasibility.status.value == "REJECT"


def test_overall_status_case_c_all_axes_pass_overrides_llm_reframe(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    provider = FakeLLMProvider([_response(_feasibility_json(overall="REFRAME"))])
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    result = feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))
    assert result.feasibility.status.value == "PASS"


# ---------------------------------------------------------------------------
# Section 32: invalid start state
# ---------------------------------------------------------------------------


def test_feasibility_invalid_start_state(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)
    provider = FakeLLMProvider([])

    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(EngineStateError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.R1_RESEARCH


# ---------------------------------------------------------------------------
# Section 33: missing idea or R0
# ---------------------------------------------------------------------------


def test_feasibility_missing_idea_candidate(engine):
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    provider = FakeLLMProvider([])
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingIdeaArtifactError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_feasibility_missing_research_r0(engine):
    idea = _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    provider = FakeLLMProvider([])
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingResearchArtifactError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_feasibility_mismatched_idea_artifact_id(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    update_artifact_reference(engine, project_id, "idea_candidate_id", uuid4())

    provider = FakeLLMProvider([])
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingIdeaArtifactError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_feasibility_mismatched_research_artifact_id(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    update_artifact_reference(engine, project_id, "research_r0_id", uuid4())

    provider = FakeLLMProvider([])
    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(MissingResearchArtifactError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 34: structured retry
# ---------------------------------------------------------------------------


def test_feasibility_structured_retry_then_success(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    invalid_json = '{"status": "PASS"}'
    provider = FakeLLMProvider([_response(invalid_json), _response(_feasibility_json())])

    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    result = feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.generation_attempts == 2

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.SUCCESS

    stored = get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
    assert stored == result.feasibility


# ---------------------------------------------------------------------------
# Section 35: LLM failure
# ---------------------------------------------------------------------------


def test_feasibility_llm_failure_marks_module_run_failed(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])

    feasibility_engine = FeasibilityEngine(engine, provider, _global_config(), _llm_settings())
    with pytest.raises(LLMProviderError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.FEASIBILITY
    assert project.feasibility_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)


# ---------------------------------------------------------------------------
# Section 36: structured exhaustion
# ---------------------------------------------------------------------------


def test_feasibility_structured_exhaustion(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    invalid_json = '{"status": "PASS"}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(invalid_json), _response(invalid_json)]
    )

    feasibility_engine = FeasibilityEngine(
        engine, provider, _global_config(), _llm_settings(max_structured_retries=2)
    )
    with pytest.raises(StructuredOutputExhaustedError):
        feasibility_engine.run(FeasibilityEngineInput(project_id=project_id))

    assert provider.call_count == 3

    project = get_project(engine, project_id)
    assert project.state == ProjectState.FEASIBILITY
    assert project.feasibility_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport)
