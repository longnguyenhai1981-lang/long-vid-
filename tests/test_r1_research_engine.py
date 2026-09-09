from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.config.loader import load_global_config
from app.engines.errors import EngineStateError
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.engine import R1ResearchEngine
from app.engines.research_r1.errors import (
    FeasibilityNotPassedError,
    MissingFeasibilityArtifactError,
    MissingIdeaArtifactError,
    MissingResearchR0ArtifactError,
    R1BusinessValidationError,
)
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE, R1ResearchInput
from app.engines.research_r1.queries import MAX_R1_SEARCH_QUERIES
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import GateEvaluation, ModuleRunStatus, PrimaryPayoff, ProjectState
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.project import Project
from app.models.research import ResearchPackage, ResearchR0
from app.research.errors import ResearchRetrieverError
from app.research.fake import FakeResearchRetriever
from app.research.models import ResearchQuery, ResearchSearchResponse, RetrievedSource
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
        recommendation="CONTINUE",
    )
    kwargs.update(overrides)
    return ResearchR0(**kwargs)


def _valid_feasibility_report(status: str = "PASS") -> FeasibilityReport:
    return FeasibilityReport(
        status=status,
        audience=SubEvaluation(status=status, reason="r"),
        science=SubEvaluation(status=status, reason="r"),
        narrative=SubEvaluation(status=status, reason="r"),
        visual=SubEvaluation(status=status, reason="r"),
        production=ProductionEvaluation(status=status, estimated_complexity="LOW", reason="r"),
    )


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01 - Tacoma Narrows", created_at=created, updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


def _create_project_in_r1_research(engine, idea=None, research_r0=None, feasibility_status="PASS"):
    idea = idea or _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)

    research_r0 = research_r0 or _valid_research_r0(idea.id)
    save_artifact(engine, project_id, RESEARCH_R0_ARTIFACT_TYPE, research_r0)
    update_artifact_reference(engine, project_id, "research_r0_id", research_r0.id)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)

    feasibility = _valid_feasibility_report(status=feasibility_status)
    save_artifact(engine, project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, feasibility)
    update_artifact_reference(engine, project_id, "feasibility_id", feasibility.id)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    return project_id, idea, research_r0, feasibility


def _evidence_response(urls: list[str], titles: list[str] | None = None) -> ResearchSearchResponse:
    titles = titles or [f"Source {i + 1}" for i in range(len(urls))]
    results = [RetrievedSource(title=t, url=u) for t, u in zip(titles, urls)]
    return ResearchSearchResponse(query=ResearchQuery(query="placeholder"), results=results)


def _claim_dict(claim_id="C001", status="SAFE", source_ids=None):
    return {
        "claim_id": claim_id,
        "claim": "Flutter is a self-excited aerodynamic oscillation",
        "status": status,
        "confidence": "HIGH",
        "source_ids": source_ids if source_ids is not None else ["S001"],
    }


def _source_dict(source_id="S001", url="https://real.example/source", supports_claims=None):
    return {
        "source_id": source_id,
        "title": "Real Paper",
        "url": url,
        "type": "paper",
        "quality_tier": 1,
        "authoritative": True,
        "supports_claims": supports_claims if supports_claims is not None else ["C001"],
    }


def _package_json(claims=None, sources=None, central_question="A question the model invented itself") -> str:
    return json.dumps(
        {
            "central_question": central_question,
            "executive_summary": "Flutter caused the bridge to collapse.",
            "timeline": ["1940: bridge collapses"],
            "physics_core": "Self-excited aeroelastic flutter",
            "claims": claims if claims is not None else [_claim_dict()],
            "disputed_points": [],
            "misconceptions": [],
            "simplification_boundary": (
                "1. safe_model: flutter as a self-reinforcing oscillation. "
                "2. allowed_simplifications: skip the full differential equations. "
                "3. omitted_complexity: torsional mode coupling details. "
                "4. dangerous_oversimplifications: do not call it simple mechanical resonance."
            ),
            "prohibited_claims": [],
            "sources": sources if sources is not None else [_source_dict()],
        }
    )


# ---------------------------------------------------------------------------
# Section 36: success
# ---------------------------------------------------------------------------


def test_r1_success(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)
    provider = FakeLLMProvider([_response(_package_json())])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert retriever.call_count <= MAX_R1_SEARCH_QUERIES
    assert provider.call_count == 1
    assert isinstance(result.research, ResearchPackage)
    assert result.research.central_question == idea.central_question

    stored = get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
    assert stored == result.research

    project = get_project(engine, project_id)
    assert project.research_r1_id == result.research.id
    assert project.state == ProjectState.NARRATIVE

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.SUCCESS
    assert runs[0].output_id == str(result.research.id)
    assert result.business_correction_used is False


# ---------------------------------------------------------------------------
# Section 38: URL deduplication
# ---------------------------------------------------------------------------


def test_r1_deduplicates_source_urls_across_queries(engine):
    idea = _valid_idea_candidate(research_questions=["Extra open question"])
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine, idea=idea)
    shared = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([shared] * MAX_R1_SEARCH_QUERIES)
    provider = FakeLLMProvider([_response(_package_json())])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert result.retrieved_source_count == 1
    sent_request = provider.received_requests[0]
    assert sent_request.user_prompt.count("https://real.example/source") == 1


# ---------------------------------------------------------------------------
# Section 39: unknown source URL
# ---------------------------------------------------------------------------


def test_r1_unknown_source_url_corrected_successfully(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)

    bad = _response(_package_json(sources=[_source_dict(url="https://invented.example/fake")]))
    good = _response(_package_json())
    provider = FakeLLMProvider([bad, good])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert result.research.sources[0].url == "https://real.example/source"

    project = get_project(engine, project_id)
    assert project.state == ProjectState.NARRATIVE


def test_r1_unknown_source_url_fails_cleanly_after_correction(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)

    bad = _response(_package_json(sources=[_source_dict(url="https://invented.example/fake")]))
    still_bad = _response(_package_json(sources=[_source_dict(url="https://still-invented.example/fake2")]))
    provider = FakeLLMProvider([bad, still_bad])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(R1BusinessValidationError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 2

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH
    assert project.research_r1_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)


# ---------------------------------------------------------------------------
# Section 40/41/42: referential integrity + duplicate ids trigger correction
# ---------------------------------------------------------------------------


def test_r1_broken_claim_source_id_triggers_correction(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)

    bad = _response(_package_json(claims=[_claim_dict(source_ids=["S999"])]))
    good = _response(_package_json())
    provider = FakeLLMProvider([bad, good])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert "unknown source_id" in provider.received_requests[1].user_prompt


def test_r1_broken_source_claim_id_triggers_correction(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)

    bad = _response(_package_json(sources=[_source_dict(supports_claims=["C999"])]))
    good = _response(_package_json())
    provider = FakeLLMProvider([bad, good])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.business_correction_used is True
    assert "unknown claim_id" in provider.received_requests[1].user_prompt


def test_r1_duplicate_claim_id_triggers_correction(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)

    bad = _response(
        _package_json(claims=[_claim_dict(claim_id="C001"), _claim_dict(claim_id="C001")])
    )
    good = _response(_package_json())
    provider = FakeLLMProvider([bad, good])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 2
    assert "Duplicate claim_id" in provider.received_requests[1].user_prompt


# ---------------------------------------------------------------------------
# Section 43: claim evidence requirement (engine-level smoke test)
# ---------------------------------------------------------------------------


def test_r1_safe_claim_without_source_triggers_correction(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)

    bad = _response(_package_json(claims=[_claim_dict(status="SAFE", source_ids=[])]))
    good = _response(_package_json())
    provider = FakeLLMProvider([bad, good])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 2
    assert "require at least one" in provider.received_requests[1].user_prompt


# ---------------------------------------------------------------------------
# Section 44: central question override
# ---------------------------------------------------------------------------


def test_r1_central_question_always_overridden(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)
    provider = FakeLLMProvider(
        [_response(_package_json(central_question="A completely different question"))]
    )

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    result = r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 1  # no correction call needed solely for this
    assert result.research.central_question == idea.central_question


# ---------------------------------------------------------------------------
# Section 45: invalid start state
# ---------------------------------------------------------------------------


def test_r1_invalid_start_state(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    update_project_state(engine, project_id, ProjectState.NARRATIVE)
    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(EngineStateError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert retriever.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 46: feasibility not PASS
# ---------------------------------------------------------------------------


def test_r1_feasibility_not_passed(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(
        engine, feasibility_status="REFRAME"
    )
    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(FeasibilityNotPassedError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert retriever.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 47: missing prior artifacts
# ---------------------------------------------------------------------------


def test_r1_missing_idea_candidate(engine):
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])
    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(MissingIdeaArtifactError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_r1_missing_research_r0(engine):
    idea = _valid_idea_candidate()
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)
    save_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, idea)
    update_artifact_reference(engine, project_id, "idea_candidate_id", idea.id)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    update_project_state(engine, project_id, ProjectState.R0_RESEARCH)
    update_project_state(engine, project_id, ProjectState.FEASIBILITY)
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])
    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(MissingResearchR0ArtifactError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


def test_r1_missing_feasibility_report(engine):
    idea = _valid_idea_candidate()
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
    update_project_state(engine, project_id, ProjectState.R1_RESEARCH)

    retriever = FakeResearchRetriever([])
    provider = FakeLLMProvider([])
    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(MissingFeasibilityArtifactError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 0
    assert list_module_runs_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Section 48: retriever failure
# ---------------------------------------------------------------------------


def test_r1_retriever_failure_prevents_llm_call(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    retriever = FakeResearchRetriever([ResearchRetrieverError("search API down")])
    provider = FakeLLMProvider([_response(_package_json())])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(ResearchRetrieverError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 0

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)


# ---------------------------------------------------------------------------
# Section 49: LLM failure
# ---------------------------------------------------------------------------


def test_r1_llm_failure_marks_module_run_failed(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)
    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])

    r1_engine = R1ResearchEngine(engine, provider, retriever, _global_config(), _llm_settings())
    with pytest.raises(LLMProviderError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH
    assert project.research_r1_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED
    assert "upstream timeout" in runs[0].error_message

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)


# ---------------------------------------------------------------------------
# Section 50: structured exhaustion
# ---------------------------------------------------------------------------


def test_r1_structured_exhaustion(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    retriever = FakeResearchRetriever([evidence] * MAX_R1_SEARCH_QUERIES)
    invalid_json = '{"physics_core": "x"}'
    provider = FakeLLMProvider(
        [_response(invalid_json), _response(invalid_json), _response(invalid_json)]
    )

    r1_engine = R1ResearchEngine(
        engine, provider, retriever, _global_config(), _llm_settings(max_structured_retries=2)
    )
    with pytest.raises(StructuredOutputExhaustedError):
        r1_engine.run(R1ResearchInput(project_id=project_id))

    assert provider.call_count == 3

    project = get_project(engine, project_id)
    assert project.state == ProjectState.R1_RESEARCH
    assert project.research_r1_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, RESEARCH_R1_ARTIFACT_TYPE, ResearchPackage)
