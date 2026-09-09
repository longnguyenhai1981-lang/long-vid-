"""Phase 34 focused tests: the twelve upstream NodeAdapters
(app/production_adapters/upstream.py) -- requirement #40.

For each adapter: correct required-artifact resolution, correct engine
invocation via a FakeLLMProvider/FakeResearchRetriever (never a live API),
the exact artifact produced, freshness reuse once produced, and a stale
upstream (simulated by replacing an upstream artifact's own id, exactly
like Phase 33's own stale-artifact tests) forcing is_fresh() back to
False. Each test reuses the corresponding engine's OWN existing test
fixtures (state-setup helper + canned LLM response builder) rather than
re-deriving them.
"""

from __future__ import annotations

from uuid import uuid4

from app.engines.idea.models import DiscoveryMode, IdeaEngineInput
from app.llm.models import LLMResponse
from app.models.assembly import AssemblyPlan
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.research import ResearchPackage, ResearchR0
from app.models.script import ScriptPlan, ScriptVerificationReport
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan
from app.orchestration.registry import ExecutionContext
from app.production_adapters.upstream import (
    AssemblyPlanAdapter,
    FeasibilityAdapter,
    IdeaAdapter,
    NarrativeAdapter,
    PackagingP0Adapter,
    PackagingP1Adapter,
    ResearchR0Adapter,
    ResearchR1Adapter,
    ScriptAdapter,
    ScriptVerifyAdapter,
    VisualPlanAdapter,
    VoicePlanAdapter,
)
from app.llm.fake import FakeLLMProvider
from app.research.fake import FakeResearchRetriever
from app.storage.artifacts import save_artifact
from tests.test_assembly_plan_engine import _create_project_at_mvp_complete as _create_project_for_assembly
from tests.test_feasibility_engine import _create_project_in_feasibility, _feasibility_json
from tests.test_idea_engine import VALID_IDEA_JSON, _create_project_in_idea_discovery, _llm_settings
from tests.test_narrative_engine import _create_project_in_narrative, _plan_json
from tests.test_packaging_p0_engine import _create_project_in_packaging_p0, _prototype_json
from tests.test_packaging_p1_engine import (
    _final_packaging_plan_json,
    _setup_project_with_full_production_stack,
)
from tests.test_r0_research_engine import _create_project_in_r0_research, _evidence_response, _research_json
from tests.test_r1_research_engine import _create_project_in_r1_research, _package_json
from tests.test_script_engine import _create_project_in_script, _script_json
from tests.test_script_verification_engine import _create_project_in_script_verification, _report_json
from tests.test_visual_plan_engine import (
    _create_project_at_mvp_complete as _create_project_for_visual_plan,
    _visual_beat_dict,
    _visual_plan_json,
)
from tests.test_voice_plan_engine import (
    _create_project_at_mvp_complete as _create_project_for_voice_plan,
    _voice_plan_json,
)


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _ctx(engine, project_id, *, provider=None, retriever=None) -> ExecutionContext:
    return ExecutionContext(
        db_engine=engine,
        project_id=project_id,
        llm_provider=provider,
        llm_settings=_llm_settings(),
        research_retriever=retriever,
    )


# ---------------------------------------------------------------------------
# 1. IDEA
# ---------------------------------------------------------------------------


def test_idea_adapter_executes_and_is_always_fresh(engine):
    project_id = _create_project_in_idea_discovery(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(VALID_IDEA_JSON)]))
    ctx.initial_idea_input = IdeaEngineInput(project_id=project_id, discovery_mode=DiscoveryMode.OPEN)
    adapter = IdeaAdapter()

    assert adapter.load_current(ctx) is None
    result = adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, IdeaCandidate)
    assert result.module_run_id is not None
    assert adapter.is_fresh(current, ctx) is True  # root -- always fresh once it exists


# ---------------------------------------------------------------------------
# 2. RESEARCH_R0 (FK-based freshness: ResearchR0.idea_id)
# ---------------------------------------------------------------------------


def test_research_r0_adapter_executes_reuses_and_detects_stale_idea(engine):
    project_id, idea = _create_project_in_r0_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider([_response(_research_json(idea.id))]),
        retriever=FakeResearchRetriever([evidence] * 10),
    )
    adapter = ResearchR0Adapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, ResearchR0)
    assert current.idea_id == idea.id
    assert adapter.is_fresh(current, ctx) is True

    stale_idea = idea.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "idea_candidate", stale_idea)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 3. FEASIBILITY (ModuleRun input_ids-based freshness -- no FK field at all)
# ---------------------------------------------------------------------------


def test_feasibility_adapter_executes_reuses_and_detects_stale_research(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_feasibility_json())]))
    adapter = FeasibilityAdapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, FeasibilityReport)
    assert adapter.is_fresh(current, ctx) is True
    assert adapter.gate_ok(current) is True  # _feasibility_json() defaults to overall PASS

    stale_research = research.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "research_r0", stale_research)
    assert adapter.is_fresh(current, ctx) is False


def test_feasibility_adapter_gate_not_ok_when_not_passed(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    # derive_overall_status recomputes the overall status from the per-axis
    # statuses -- the top-level "status" field alone is not authoritative.
    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider([_response(_feasibility_json(axis_statuses={"science": "REFRAME"}, overall="REFRAME"))]),
    )
    adapter = FeasibilityAdapter()

    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert adapter.gate_ok(current) is False


# ---------------------------------------------------------------------------
# 4. RESEARCH_R1
# ---------------------------------------------------------------------------


def test_research_r1_adapter_executes_reuses_and_detects_stale_feasibility(engine):
    project_id, idea, research_r0, feasibility = _create_project_in_r1_research(engine)
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider([_response(_package_json())]),
        retriever=FakeResearchRetriever([evidence] * 10),
    )
    adapter = ResearchR1Adapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, ResearchPackage)
    assert adapter.is_fresh(current, ctx) is True

    stale_feasibility = feasibility.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "feasibility_report", stale_feasibility)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 5. NARRATIVE
# ---------------------------------------------------------------------------


def test_narrative_adapter_executes_reuses_and_detects_stale_research(engine):
    project_id, idea, research_package = _create_project_in_narrative(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_plan_json())]))
    adapter = NarrativeAdapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, NarrativePlan)
    assert adapter.is_fresh(current, ctx) is True

    stale_research = research_package.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "research_r1", stale_research)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 6. PACKAGING_P0
# ---------------------------------------------------------------------------


def test_packaging_p0_adapter_executes_reuses_and_detects_stale_narrative(engine):
    project_id, idea, research_package, narrative_plan = _create_project_in_packaging_p0(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_prototype_json())]))
    adapter = PackagingP0Adapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, PackagingPrototype)
    assert adapter.is_fresh(current, ctx) is True
    assert adapter.gate_ok(current) is True  # default risk LOW

    stale_narrative = narrative_plan.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "narrative_plan", stale_narrative)
    assert adapter.is_fresh(current, ctx) is False


def test_packaging_p0_adapter_gate_not_ok_when_risk_high(engine):
    project_id, idea, research_package, narrative_plan = _create_project_in_packaging_p0(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_prototype_json(risk="HIGH"))]))
    adapter = PackagingP0Adapter()

    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert adapter.gate_ok(current) is False


# ---------------------------------------------------------------------------
# 7. SCRIPT
# ---------------------------------------------------------------------------


def test_script_adapter_executes_reuses_and_detects_stale_packaging(engine):
    project_id, idea, research_package, narrative_plan, packaging = _create_project_in_script(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_script_json())]))
    adapter = ScriptAdapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, ScriptPlan)
    assert adapter.is_fresh(current, ctx) is True

    stale_packaging = packaging.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "packaging_prototype", stale_packaging)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 8. SCRIPT_VERIFY (no `id` field at all on ScriptVerificationReport)
# ---------------------------------------------------------------------------


def test_script_verify_adapter_executes_reuses_and_detects_stale_script(engine):
    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_report_json())]))
    adapter = ScriptVerifyAdapter()

    assert adapter.load_current(ctx) is None
    result = adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, ScriptVerificationReport)
    assert not hasattr(current, "id")
    assert result.artifact_id == result.module_run_id  # no real artifact id to report
    assert adapter.is_fresh(current, ctx) is True
    assert adapter.gate_ok(current) is True  # _report_json() defaults to PASS

    stale_script = script_plan.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "script_plan", stale_script)
    assert adapter.is_fresh(current, ctx) is False


def test_script_verify_adapter_gate_not_ok_when_not_passed(engine):
    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    # normalize_status() forces PASS whenever no issue lists are populated,
    # regardless of the raw "status" field -- a real REJECT needs issues.
    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider([_response(_report_json(status="REJECT", unsupported_lines=["L001"]))]),
    )
    adapter = ScriptVerifyAdapter()

    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert adapter.gate_ok(current) is False


# ---------------------------------------------------------------------------
# 9. VOICE_PLAN (FK-based freshness: VoicePlan.script_plan_id)
# ---------------------------------------------------------------------------


def test_voice_plan_adapter_executes_reuses_and_detects_stale_script(engine):
    project_id, script_plan = _create_project_for_voice_plan(engine)
    ctx = _ctx(engine, project_id, provider=FakeLLMProvider([_response(_voice_plan_json(script_plan.id))]))
    adapter = VoicePlanAdapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, VoicePlan)
    assert current.script_plan_id == script_plan.id
    assert adapter.is_fresh(current, ctx) is True

    stale_script = script_plan.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "script_plan", stale_script)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 10. VISUAL_PLAN
# ---------------------------------------------------------------------------


def test_visual_plan_adapter_executes_reuses_and_detects_stale_voice_plan(engine):
    project_id, research_package, narrative_plan, script_plan = _create_project_for_visual_plan(engine)
    from tests.test_visual_plan_engine import _valid_voice_plan

    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, "voice_plan", voice_plan)

    beats = [
        _visual_beat_dict("VB001", ["L001", "L002"], media_type="GENERATED_STILL"),
        _visual_beat_dict("VB002", ["L003", "L004"], media_type="GENERATED_STILL"),
    ]
    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider([_response(_visual_plan_json(script_plan.id, voice_plan.id, beats=beats))]),
    )
    adapter = VisualPlanAdapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, VisualPlan)
    assert adapter.is_fresh(current, ctx) is True

    stale_voice = voice_plan.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "voice_plan", stale_voice)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 11. ASSEMBLY_PLAN
# ---------------------------------------------------------------------------


def test_assembly_plan_adapter_executes_reuses_and_detects_stale_visual_plan(engine):
    from tests.test_assembly_plan_engine import _assembly_plan_json, _valid_visual_plan, _valid_voice_plan

    project_id, script_plan = _create_project_for_assembly(engine)
    voice_plan = _valid_voice_plan(script_plan.id)
    save_artifact(engine, project_id, "voice_plan", voice_plan)
    visual_plan = _valid_visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, "visual_plan", visual_plan)

    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider(
            [_response(_assembly_plan_json(script_plan.id, voice_plan.id, visual_plan.id))]
        ),
    )
    adapter = AssemblyPlanAdapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, AssemblyPlan)
    assert adapter.is_fresh(current, ctx) is True

    stale_visual = visual_plan.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "visual_plan", stale_visual)
    assert adapter.is_fresh(current, ctx) is False


# ---------------------------------------------------------------------------
# 12. PACKAGING_P1
# ---------------------------------------------------------------------------


def test_packaging_p1_adapter_executes_reuses_and_detects_stale_assembly_plan(engine):
    project_id, packaging, research_package, narrative_plan, script_plan, voice_plan, visual_plan, assembly_plan = (
        _setup_project_with_full_production_stack(engine)
    )
    ctx = _ctx(
        engine, project_id,
        provider=FakeLLMProvider(
            [_response(_final_packaging_plan_json(packaging.id, script_plan.id, visual_plan.id, assembly_plan.id))]
        ),
    )
    adapter = PackagingP1Adapter()

    assert adapter.load_current(ctx) is None
    adapter.execute(ctx)
    current = adapter.load_current(ctx)
    assert isinstance(current, FinalPackagingPlan)
    assert adapter.is_fresh(current, ctx) is True

    stale_assembly = assembly_plan.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "assembly_plan", stale_assembly)
    assert adapter.is_fresh(current, ctx) is False
