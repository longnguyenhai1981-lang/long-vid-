from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.loader import load_global_config
from app.engines.errors import EngineStateError
from app.engines.idea.engine import IdeaEngine
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE, DiscoveryMode, IdeaEngineInput
from app.llm.config import LLMSettings
from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import ModuleRunStatus, ProjectState
from app.models.idea import IdeaCandidate
from app.models.project import Project
from app.storage.approvals import list_approvals_for_project
from app.storage.artifacts import get_artifact
from app.storage.errors import ArtifactNotFoundError
from app.storage.module_runs import list_module_runs_for_project
from app.storage.projects import create_project, get_project, update_project_state

VALID_IDEA_JSON = """
{
  "topic": "Cau Tacoma Narrows",
  "central_question": "Vi sao mot cay cau vung chac lai sup do vi gio nhe?",
  "abt": {
    "and_context": "Ky su tin rang cau treo da du an toan voi tai trong va gio thong thuong",
    "but_complication": "Cau Tacoma Narrows rung lac du doi roi sup do duoi con gio vua phai, khong phai bao",
    "therefore_investigation": "Dieu tra co che cong huong khi dong hoc an giau dang sau"
  },
  "primary_payoff": "REVERSAL",
  "secondary_payoffs": ["DISCOVERY"],
  "physics_core": "Cong huong khi dong hoc tu kich thich (aeroelastic flutter)",
  "audience_prerequisite": "none",
  "brand_fit": {"status": "PASS", "reason": "Kich tinh hinh anh manh, de ke chuyen"},
  "general_audience_gate": {"status": "PASS", "reason": "Khong can kien thuc vat ly nang cao"},
  "longform_potential": {"status": "PASS", "reason": "Du chat lieu dieu tra 8-10 phut"},
  "research_questions": ["Co che flutter khi dong hoc hoat dong the nao?"],
  "risks": ["De nham voi cong huong co hoc don gian"]
}
"""

INVALID_SCHEMA_JSON = '{"topic": "x"}'


def _global_config():
    return load_global_config()


def _llm_settings(max_structured_retries: int = 2) -> LLMSettings:
    return LLMSettings(
        provider="fake", default_model="fake-model", max_structured_retries=max_structured_retries
    )


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _create_project_in_idea_discovery(engine, **overrides):
    created = datetime.now(timezone.utc)
    kwargs = dict(
        title_internal="Ep01 - Cau Tacoma Narrows",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    kwargs.update(overrides)
    project = Project(**kwargs)
    create_project(engine, project)
    update_project_state(engine, project.project_id, ProjectState.IDEA_DISCOVERY)
    return project.project_id


# ---------------------------------------------------------------------------
# Section 24: successful vertical slice
# ---------------------------------------------------------------------------


def test_successful_idea_vertical_slice(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    result = idea_engine.run(
        IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN)
    )

    assert isinstance(result.idea, IdeaCandidate)
    assert provider.call_count == 1

    stored_idea = get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
    assert stored_idea == result.idea

    project = get_project(engine, project_id)
    assert project.idea_candidate_id == result.idea.id
    assert project.state == ProjectState.IDEA_REVIEW

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].run_id == result.module_run_id
    assert runs[0].status == ModuleRunStatus.SUCCESS
    assert runs[0].output_id == str(result.idea.id)


def test_idea_engine_result_carries_traceability_metadata(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    result = idea_engine.run(
        IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN)
    )

    assert result.provider == "fake"
    assert result.model == "fake-model"
    assert result.generation_attempts == 1
    assert result.module_run_id is not None


# ---------------------------------------------------------------------------
# Section 25: invalid start state
# ---------------------------------------------------------------------------


def test_invalid_start_state_new_project_raises_engine_state_error(engine):
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Ep01", created_at=created, updated_at=created, state=ProjectState.NEW_PROJECT
    )
    create_project(engine, project)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    with pytest.raises(EngineStateError):
        idea_engine.run(
            IdeaEngineInput(project_id=project.project_id, discovery_mode=DiscoveryMode.OPEN)
        )

    assert provider.call_count == 0
    fetched = get_project(engine, project.project_id)
    assert fetched.state == ProjectState.NEW_PROJECT
    assert fetched.idea_candidate_id is None
    assert list_module_runs_for_project(engine, project.project_id) == []
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)


def test_invalid_start_state_idea_review_raises_engine_state_error(engine):
    project_id = _create_project_in_idea_discovery(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_REVIEW)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    with pytest.raises(EngineStateError):
        idea_engine.run(IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN))

    assert provider.call_count == 0
    fetched = get_project(engine, project_id)
    assert fetched.state == ProjectState.IDEA_REVIEW
    assert fetched.idea_candidate_id is None
    assert list_module_runs_for_project(engine, project_id) == []
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)


# ---------------------------------------------------------------------------
# Section 26: structured retry
# ---------------------------------------------------------------------------


def test_structured_retry_then_success(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(INVALID_SCHEMA_JSON), _response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    result = idea_engine.run(
        IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN)
    )

    assert provider.call_count == 2
    assert result.generation_attempts == 2

    stored_idea = get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)
    assert stored_idea == result.idea

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_REVIEW

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.SUCCESS


# ---------------------------------------------------------------------------
# Section 27: LLM provider failure
# ---------------------------------------------------------------------------


def test_llm_provider_failure_marks_module_run_failed(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([LLMProviderError("upstream timeout")])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    with pytest.raises(LLMProviderError):
        idea_engine.run(IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_DISCOVERY
    assert project.idea_candidate_id is None
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate)

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED
    assert runs[0].error_message
    assert "upstream timeout" in runs[0].error_message


# ---------------------------------------------------------------------------
# Section 28: retry exhaustion
# ---------------------------------------------------------------------------


def test_retry_exhaustion_marks_module_run_failed(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider(
        [
            _response(INVALID_SCHEMA_JSON),
            _response(INVALID_SCHEMA_JSON),
            _response(INVALID_SCHEMA_JSON),
        ]
    )
    idea_engine = IdeaEngine(
        engine, provider, _global_config(), _llm_settings(max_structured_retries=2)
    )

    with pytest.raises(StructuredOutputExhaustedError):
        idea_engine.run(IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN))

    assert provider.call_count == 3

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_DISCOVERY
    assert project.idea_candidate_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED


# ---------------------------------------------------------------------------
# Section 29: storage failure after successful generation
# ---------------------------------------------------------------------------


def test_artifact_storage_failure_leaves_project_in_idea_discovery(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])

    def failing_save_artifact(db_engine, proj_id, artifact_type, model, schema_version="0.1"):
        raise RuntimeError("simulated disk failure")

    stub_artifact_repo = SimpleNamespace(save_artifact=failing_save_artifact)
    idea_engine = IdeaEngine(
        engine, provider, _global_config(), _llm_settings(), artifact_repo=stub_artifact_repo
    )

    with pytest.raises(RuntimeError, match="simulated disk failure"):
        idea_engine.run(IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_DISCOVERY
    assert project.idea_candidate_id is None

    runs = list_module_runs_for_project(engine, project_id)
    assert len(runs) == 1
    assert runs[0].status == ModuleRunStatus.FAILED


# ---------------------------------------------------------------------------
# Section 30/31: EXPAND vs OPEN mode
# ---------------------------------------------------------------------------


def test_expand_mode_sends_seed_to_provider(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    idea_engine.run(
        IdeaEngineInput(
            project_id=project_id, discovery_mode=DiscoveryMode.EXPAND, seed="Tacoma Narrows Bridge"
        )
    )

    assert provider.call_count == 1
    sent_request = provider.received_requests[0]
    assert "Tacoma Narrows Bridge" in sent_request.user_prompt
    assert "EXPAND" in sent_request.user_prompt
    assert "develop" in sent_request.user_prompt.lower()


def test_open_mode_runs_and_instructs_discovery(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    result = idea_engine.run(
        IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN)
    )

    assert isinstance(result.idea, IdeaCandidate)
    sent_request = provider.received_requests[0]
    assert "OPEN" in sent_request.user_prompt
    assert "discover" in sent_request.user_prompt.lower()


# ---------------------------------------------------------------------------
# Section 33: no automatic human approval / no auto-advance past IDEA_REVIEW
# ---------------------------------------------------------------------------


def test_no_automatic_human_approval_or_further_advance(engine):
    project_id = _create_project_in_idea_discovery(engine)
    provider = FakeLLMProvider([_response(VALID_IDEA_JSON)])
    idea_engine = IdeaEngine(engine, provider, _global_config(), _llm_settings())

    idea_engine.run(IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN))

    project = get_project(engine, project_id)
    assert project.state == ProjectState.IDEA_REVIEW
    assert list_approvals_for_project(engine, project_id) == []
