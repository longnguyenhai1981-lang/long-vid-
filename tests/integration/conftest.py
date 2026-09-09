"""Phase 12 integration fixtures.

Each `project_at_*` fixture advances one coherent Tacoma-Narrows-Bridge
project through the real, public engine/review operations -- the same
operations a test (or, eventually, a human/orchestrator) would call -- one
stage further than the previous fixture. This is test *setup* via the
existing public APIs, exactly like `tests/test_review_service.py`'s
`_create_project_in_..._with_...` helpers; it is not a new pipeline/
orchestrator abstraction (see pipeline_helpers.py's module docstring and
Phase 12 section 30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.config.loader import GlobalConfig, load_global_config
from app.engines.feasibility.engine import FeasibilityEngine
from app.engines.feasibility.models import FeasibilityEngineInput
from app.engines.idea.engine import IdeaEngine
from app.engines.idea.models import IdeaEngineInput
from app.engines.narrative.engine import NarrativeEngine
from app.engines.narrative.models import NarrativeEngineInput
from app.engines.packaging_p0.engine import PackagingP0Engine
from app.engines.packaging_p0.models import PackagingP0Input
from app.engines.research_r0.engine import R0ResearchEngine
from app.engines.research_r0.models import R0ResearchInput
from app.engines.research_r1.engine import R1ResearchEngine
from app.engines.research_r1.models import R1ResearchInput
from app.engines.script.engine import ScriptEngine
from app.engines.script.models import ScriptEngineInput
from app.engines.script_verification.engine import ScriptVerificationEngine
from app.engines.script_verification.models import ScriptVerificationInput
from app.llm.config import LLMSettings
from app.llm.fake import FakeLLMProvider
from app.models.common import GateStatus, ProjectState
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import ResearchPackage, ResearchR0
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.research.fake import FakeResearchRetriever
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
)
from app.storage.projects import create_project, update_project_state
from tests.integration import pipeline_helpers as fx


@dataclass
class PipelineState:
    project_id: UUID
    idea: IdeaCandidate | None = None
    research_r0: ResearchR0 | None = None
    feasibility: FeasibilityReport | None = None
    research_package: ResearchPackage | None = None
    narrative_plan: NarrativePlan | None = None
    packaging: PackagingPrototype | None = None
    script_plan: ScriptPlan | None = None
    verification_report: ScriptVerificationReport | None = None


@pytest.fixture()
def global_config() -> GlobalConfig:
    return load_global_config()


@pytest.fixture()
def llm_settings() -> LLMSettings:
    return fx.llm_settings()


def _new_project(engine) -> UUID:
    created = datetime.now(timezone.utc)
    project = Project(
        title_internal="Tacoma Narrows E2E",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


@pytest.fixture()
def project_at_idea_review(engine, global_config, llm_settings) -> PipelineState:
    project_id = _new_project(engine)
    update_project_state(engine, project_id, ProjectState.IDEA_DISCOVERY)

    provider = FakeLLMProvider([fx.response(fx.idea_candidate_json())])
    idea_engine = IdeaEngine(engine, provider, global_config, llm_settings)
    result = idea_engine.run(IdeaEngineInput(project_id=project_id, discovery_mode="OPEN"))

    return PipelineState(project_id=project_id, idea=result.idea)


@pytest.fixture()
def project_at_feasibility(engine, global_config, llm_settings, project_at_idea_review) -> PipelineState:
    state = project_at_idea_review
    approve_idea(engine, state.project_id, feedback="Strong hook, approved")

    provider = FakeLLMProvider([fx.response(fx.research_r0_json())])
    retriever = FakeResearchRetriever(fx.r0_search_responses())
    r0_engine = R0ResearchEngine(engine, provider, retriever, llm_settings)
    result = r0_engine.run(R0ResearchInput(project_id=state.project_id))

    state.research_r0 = result.research
    return state


@pytest.fixture()
def project_at_r1_research(engine, global_config, llm_settings, project_at_feasibility) -> PipelineState:
    state = project_at_feasibility

    provider = FakeLLMProvider([fx.response(fx.feasibility_report_json())])
    feasibility_engine = FeasibilityEngine(engine, provider, global_config, llm_settings)
    result = feasibility_engine.run(FeasibilityEngineInput(project_id=state.project_id))
    state.feasibility = result.feasibility

    decide_feasibility(engine, state.project_id, GateStatus.PASS, feedback="Proceed to deep research")
    return state


@pytest.fixture()
def project_at_narrative_review(
    engine, global_config, llm_settings, project_at_r1_research
) -> PipelineState:
    state = project_at_r1_research

    provider = FakeLLMProvider([fx.response(fx.research_package_json())])
    retriever = FakeResearchRetriever(fx.r1_search_responses())
    r1_engine = R1ResearchEngine(engine, provider, retriever, global_config, llm_settings)
    result = r1_engine.run(R1ResearchInput(project_id=state.project_id))
    state.research_package = result.research

    provider = FakeLLMProvider([fx.response(fx.narrative_plan_json())])
    narrative_engine = NarrativeEngine(engine, provider, global_config, llm_settings)
    result = narrative_engine.run(NarrativeEngineInput(project_id=state.project_id))
    state.narrative_plan = result.narrative

    return state


@pytest.fixture()
def project_at_packaging_p0(
    engine, global_config, llm_settings, project_at_narrative_review
) -> PipelineState:
    state = project_at_narrative_review
    approve_narrative(engine, state.project_id, feedback="Strong investigative arc")
    return state


@pytest.fixture()
def project_at_script(engine, global_config, llm_settings, project_at_packaging_p0) -> PipelineState:
    state = project_at_packaging_p0

    provider = FakeLLMProvider([fx.response(fx.packaging_prototype_json())])
    packaging_engine = PackagingP0Engine(engine, provider, global_config, llm_settings)
    result = packaging_engine.run(PackagingP0Input(project_id=state.project_id))
    state.packaging = result.packaging

    approve_packaging_p0(engine, state.project_id, feedback="Promise is strong and honest")
    return state


@pytest.fixture()
def project_at_script_verification(
    engine, global_config, llm_settings, project_at_script
) -> PipelineState:
    state = project_at_script

    provider = FakeLLMProvider([fx.response(fx.script_plan_json())])
    script_engine = ScriptEngine(engine, provider, global_config, llm_settings)
    result = script_engine.run(ScriptEngineInput(project_id=state.project_id))
    state.script_plan = result.script

    return state


@pytest.fixture()
def project_at_script_review(
    engine, global_config, llm_settings, project_at_script_verification
) -> PipelineState:
    state = project_at_script_verification

    provider = FakeLLMProvider([fx.response(fx.verification_report_json(status="PASS"))])
    verification_engine = ScriptVerificationEngine(engine, provider, global_config, llm_settings)
    result = verification_engine.run(ScriptVerificationInput(project_id=state.project_id))
    state.verification_report = result.report

    accept_script_verification(engine, state.project_id, feedback="Clean verification")
    return state


@pytest.fixture()
def project_at_mvp_complete(engine, global_config, llm_settings, project_at_script_review) -> PipelineState:
    state = project_at_script_review
    approve_final_script(engine, state.project_id, feedback="Ready to publish")
    return state
