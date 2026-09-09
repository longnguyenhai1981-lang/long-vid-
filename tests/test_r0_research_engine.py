from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.engines.errors import EngineStateError
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.research_r0.engine import R0ResearchEngine
from app.engines.research_r0.errors import MissingIdeaArtifactError, ResearchSourceHallucinationError
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE, R0ResearchInput
from app.engines.research_r0.queries import MAX_R0_SEARCH_QUERIES
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import GateEvaluation, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.idea import ABT, IdeaCandidate
from app.models.project import Project
from app.models.research import ResearchR0
from app.research.errors import ResearchRetrieverError
from app.research.fake import FakeResearchRetriever
from app.research.models import ResearchQuery, ResearchSearchResponse, RetrievedSource
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.errors import ArtifactNotFoundError
from app.storage.module_runs import list_module_runs_for_project
from app.storage.projects import create_project, get_project, update_artifact_reference, update_project_state


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


def _llm_settings(max_structured_retries: int = 2) -> LLMSettings:
    return LLMSettings(
        provider="fake", default_model="fake-model", max_structured_retries=max_structured_retries
    )


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _research_json(idea_id: UUID, candidate_sources: list[str] | None = None) -> str:
    return json.dumps(
        {
            "idea_id": str(idea_id),
            "topic_valid": True,
            "credible_sources_available": True,
            "story_material_available": True,
            "physics_material_available": True,
            "initial_findings": ["Aeroelastic flutter is a documented bridge failure mode"],
            "candidate_sources": candidate_sources or [],
            "major_risks": ["Risk of conflating flutter with simple mechanical resonance"],
            "recommendation": "CONTINUE",
        }
    )


def _evidence_response(urls: list[str], titles: list[str] | None = None) -> ResearchSearchResponse:
    titles = titles or [f"Source {i + 1}" for i in range(len(urls))]
    results = [RetrievedSource(title=t, url=u) for t, u in zip(titles, urls)]
    return ResearchSearchResponse(query=ResearchQuery(query="placeholder"), results=results)


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01 - Tacoma Narrows", created_at=created, updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


def _create_project_in_idea_review(engine, idea=None):
    idea = idea or _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    return project_id, idea


def _create_project_in_r0_research(engine, idea=None):
    project_id, idea = _create_project_in_idea_review(engine, idea=idea)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    return project_id, idea


# ---------------------------------------------------------------------------
# Section 34: success
# ---------------------------------------------------------------------------


def test_r0_success(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence, evidence, evidence])
    provider = FakeLLMProvider(
        [_response(_research_json(idea.id, candidate_sources=["https://real.example/source"]))]
    )

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    result = r0_engine.run(R0ResearchInput(project_id=project_id))

    assert retriever.call_count <= MAX_R0_SEARCH_QUERIES
    assert provider.call_count == 1
    assert isinstance(result.research, ResearchR0)
    assert result.research.idea_id == idea.id

    stored = get_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)
    assert stored == result.research

    project = get_project(engine, project_id)
    assert project.research_r0_id == result.research.id
    assert project.state == ProjectState.FEASIBILITY

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.SUCCESS
    assert runs[0].output_id == str(result.research.id)


# ---------------------------------------------------------------------------
# Section 35: query bound
# ---------------------------------------------------------------------------


def test_r0_query_bound_respected(engine):
    idea = _valid_idea_candidate(
        research_questions=["Q1 about flutter", "Q2 about wind speed", "Q3 warning signs", "Q4 modern fixes"]
    )
    project_id, idea = _create_project_in_r0_research(engine, idea=idea)
    empty = _evidence_response([])
    retriever = FakeResearchRetriever([empty] * MAX_R0_SEARCH_QUERIES)
    provider = FakeLLMProvider([_response(_research_json(idea.id))])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    r0_engine.run(R0ResearchInput(project_id=project_id))

    assert retriever.call_count == MAX_R0_SEARCH_QUERIES


# ---------------------------------------------------------------------------
# Section 36: deduplication
# ---------------------------------------------------------------------------


def test_r0_deduplicates_source_urls_across_queries(engine):
    idea = _valid_idea_candidate(research_questions=["An extra research question"])
    project_id, idea = _create_project_in_r0_research(engine, idea=idea)
    shared = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([shared, shared, shared, shared])
    provider = FakeLLMProvider(
        [_response(_research_json(idea.id, candidate_sources=["https://real.example/source"]))]
    )

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    result = r0_engine.run(R0ResearchInput(project_id=project_id))

    assert retriever.call_count == 4
    assert result.retrieved_source_count == 1

    sent_request = provider.received_requests[0]
    assert sent_request.user_prompt.count("https://real.example/source") == 1


# ---------------------------------------------------------------------------
# Section 37: hallucinated source
# ---------------------------------------------------------------------------


def test_r0_hallucinated_source_corrected_successfully(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence, evidence, evidence])

    bad = _response(_research_json(idea.id, candidate_sources=["https://invented.example/fake"]))
    good = _response(_research_json(idea.id, candidate_sources=["https://real.example/source"]))
    provider = FakeLLMProvider([bad, good])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    result = r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.research.candidate_sources == ["https://real.example/source"]
    assert result.generation_attempts == 2

    correction_request = provider.received_requests[1]
    assert "invented.example" in correction_request.user_prompt
    assert "real.example/source" in correction_request.user_prompt

    project = get_project(engine, project_id)
    assert project.state == ProjectState.FEASIBILITY
    assert project.research_r0_id == result.research.id


def test_r0_hallucinated_source_fails_cleanly_after_correction(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence, evidence, evidence])

    bad = _response(_research_json(idea.id, candidate_sources=["https://invented.example/fake"]))
    still_bad = _response(
        _research_json(idea.id, candidate_sources=["https://still-invented.example/fake2"])
    )
    provider = FakeLLMProvider([bad, still_bad])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    with pytest.raises(ResearchSourceHallucinationError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 2

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R0_RESEARCH
    assert project.research_r0_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)


# ---------------------------------------------------------------------------
# Section 38: LLM failure
# ---------------------------------------------------------------------------


def test_r0_llm_failure_marks_module_run_failed(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence, evidence, evidence])
    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    with pytest.raises(LLMProviderError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R0_RESEARCH
    assert project.research_r0_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)


# ---------------------------------------------------------------------------
# Section 39: structured exhaustion
# ---------------------------------------------------------------------------


def test_r0_structured_exhaustion(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence, evidence, evidence])
    invalid_json = '{"topic_valid": true}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(invalid_json), _response(invalid_json)]
    )

    r0_engine = R0ResearchEngine(
        engine, provider, retriever, _llm_settings(max_structured_retries=2)
    )
    with pytest.raises(StructuredOutputExhaustedError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 3

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R0_RESEARCH
    assert project.research_r0_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)


# ---------------------------------------------------------------------------
# Section 40: retriever failure
# ---------------------------------------------------------------------------


def test_r0_retriever_failure_prevents_llm_call(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    retriever = FakeResearchRetriever([ResearchRetrieverError("search API down")])
    provider = FakeLLMProvider([_response(_research_json(idea.id))])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    with pytest.raises(ResearchRetrieverError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 0

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R0_RESEARCH

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0)


# ---------------------------------------------------------------------------
# Section 41: invalid start state
# ---------------------------------------------------------------------------


def test_r0_invalid_start_state_idea_review(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    with pytest.raises(EngineStateError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert retriever.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.IDEA_REVIEW


def test_r0_invalid_start_state_feasibility(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    with pytest.raises(EngineStateError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert retriever.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY


# ---------------------------------------------------------------------------
# Section 42: missing idea
# ---------------------------------------------------------------------------


def test_r0_missing_idea_candidate_id(engine):
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)

    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])
    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())

    with pytest.raises(MissingIdeaArtifactError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert retriever.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []
    project = get_project(engine, project_id)
    assert project.state == ProjectState.R0_RESEARCH
    assert project.research_r0_id is None


def test_r0_mismatched_idea_artifact_id(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    update_artifact_reference(engine, project_id, "idea_candidate_id", uuid4())

    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])
    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())

    with pytest.raises(MissingIdeaArtifactError):
        r0_engine.run(R0ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert retriever.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 28: recommendation does not gate the transition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recommendation", ["CONTINUE", "REFRAME", "REJECT"])
def test_r0_recommendation_never_blocks_transition_to_feasibility(engine, recommendation):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence, evidence, evidence])
    payload = json.loads(_research_json(idea.id, candidate_sources=["https://real.example/source"]))
    payload["recommendation"] = recommendation
    provider = FakeLLMProvider([_response(json.dumps(payload))])

    r0_engine = R0ResearchEngine(engine, provider, retriever, _llm_settings())
    result = r0_engine.run(R0ResearchInput(project_id=project_id))

    assert result.research.recommendation.value == recommendation
    assert get_project(engine, project_id).state == ProjectState.FEASIBILITY
