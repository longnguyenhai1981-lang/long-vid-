from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.diagram_renderer.errors import DiagramRenderError
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.narrative.models import NARRATIVE_PLAN_ARTIFACT_TYPE
from app.engines.packaging_p0.models import PACKAGING_PROTOTYPE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.models import RESEARCH_R1_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.script_verification.models import SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.models.common import (
    GateEvaluation,
    GateStatus,
    ModuleRunStatus,
    PrimaryPayoff,
    ProjectState,
)
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.project import Project
from app.models.research import Claim, ResearchPackage, ResearchR0
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport
from app.models.diagram import DiagramCanvas, DiagramLine, DiagramPoint, DiagramSpec
from app.models.ti_assets import TiState
from app.models.visual import VisualBeat, VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.visual.errors import (
    InvalidVisualBeatError,
    MissingScriptPlanArtifactError,
    MissingVisualPlanArtifactError,
    MissingVoicePlanArtifactError,
    RendererStateError,
    StaleVisualPlanError,
    StaleVoicePlanError,
    TiCompositorNotConfiguredError,
    VisualRenderManifestIntegrityError,
)
from app.renderers.visual.models import (
    VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE,
    TiStateRenderMode,
    TiStateSource,
    VisualRendererInput,
)
from app.renderers.visual.renderer import VisualRenderer
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
from app.storage.projects import (
    create_project,
    get_project,
    update_artifact_reference,
    update_project_state,
)
from app.ti_assets.retriever import SqliteTiAssetRetriever
from app.ti_assets.storage import TiAssetFileStore
from app.ti_compositor.compositor import TiCompositor
from app.visual.config import VisualSettings
from app.visual.errors import VisualProviderError, VisualWriteError
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore
from tests._ti_compositor_helpers import write_full_ti_asset_set


# ---------------------------------------------------------------------------
# Fixture builders (project graph walk, mirroring tests/test_voice_renderer.py)
# ---------------------------------------------------------------------------


def _record_module_run_success(engine, project_id, module: str, input_ids: list[str]):
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
        title_internal="Ep01 - Tacoma Narrows",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    return project.project_id


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
        ],
        qa_status="PASS",
    )


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
        engine,
        project_id,
        "packaging_p0_engine",
        [str(idea.id), str(research_package.id), str(narrative_plan.id)],
    )
    approve_packaging_p0(engine, project_id)

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
    approve_final_script(engine, project_id)
    assert get_project(engine, project_id).state == ProjectState.MVP_COMPLETE

    return project_id, script_plan


def _save_voice_plan(engine, project_id, script_plan_id) -> VoicePlan:
    voice_plan = VoicePlan(
        script_plan_id=script_plan_id,
        chunks=[
            VoiceChunk(
                chunk_id="C001",
                line_ids=["L001", "L002"],
                voice_state="NEUTRAL",
                pace="NORMAL",
                energy="MEDIUM",
                take_count=1,
                music_state="BED",
            )
        ],
    )
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    return voice_plan


def _diagram_spec() -> DiagramSpec:
    return DiagramSpec(
        canvas=DiagramCanvas(width=100, height=100),
        elements=[DiagramLine(start=DiagramPoint(x=0.1, y=0.1), end=DiagramPoint(x=0.9, y=0.9))],
    )


def _visual_beat(beat_id, media_type="GENERATED_STILL", **overrides) -> VisualBeat:
    fields = dict(
        beat_id=beat_id,
        script_line_ids=["L001"],
        narrative_node="Q0",
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type=media_type,
        complexity="C1",
        concept=f"Concept for {beat_id}",
        primary_focus=f"Focus for {beat_id}",
    )
    if media_type == "DIAGRAM" and "diagram_spec" not in overrides:
        # DIAGRAM requires diagram_spec (Phase 25) -- default every
        # DIAGRAM-media-type test beat to a minimal valid spec so existing
        # call sites don't all need updating individually; a test that
        # cares about the spec's own content passes diagram_spec explicitly.
        fields["diagram_spec"] = _diagram_spec()
    fields.update(overrides)
    return VisualBeat(**fields)


def _save_visual_plan(engine, project_id, script_plan_id, voice_plan_id, beats) -> VisualPlan:
    visual_plan = VisualPlan(script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, beats=beats)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    return visual_plan


def _seven_beat_shortlist():
    """V1..V7 -- one beat per VisualMediaType, matching Phase 19 spec's
    happy-path test matrix exactly."""
    return [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V3", media_type="DIAGRAM"),
        _visual_beat("V4", media_type="LIMITED_MOTION", motion_intent="slow pan"),
        _visual_beat("V5", media_type="EVIDENCE_MEDIA", evidence_source_ids=["E001", "E002"]),
        _visual_beat("V6", media_type="ASSET_REUSE", reuse_key="ti_hero_shot"),
        _visual_beat("V7", media_type="AI_HERO_VIDEO"),
    ]


def _create_project_ready_for_visual_rendering(engine, beats=None):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _save_voice_plan(engine, project_id, script_plan.id)
    visual_plan = _save_visual_plan(
        engine, project_id, script_plan.id, voice_plan.id, beats or _seven_beat_shortlist()
    )
    return project_id, script_plan, voice_plan, visual_plan


def _visual_response(**overrides) -> VisualRenderResponse:
    fields = dict(asset_bytes=b"fake-png-bytes", provider="fake-visual", output_format="PNG")
    fields.update(overrides)
    return VisualRenderResponse(**fields)


def _settings(**overrides) -> VisualSettings:
    fields = dict(provider="fake-visual")
    fields.update(overrides)
    return VisualSettings(**fields)


def _ti_compositor(engine, tmp_path) -> TiCompositor:
    """A TiCompositor backed by a real, complete, active TiAssetSet
    (synthetic PNGs -- see tests/_ti_compositor_helpers.py), for tests
    exercising a TI_STATE beat. Every TiState maps to the same synthetic
    asset; these tests care about routing/reference correctness, not
    per-state visual distinction."""
    file_store = TiAssetFileStore(tmp_path / "ti_assets")
    write_full_ti_asset_set(engine, file_store)
    return TiCompositor(SqliteTiAssetRetriever(engine, file_store))


# ---------------------------------------------------------------------------
# Happy path / media routing (spec sections 26, 43)
# ---------------------------------------------------------------------------


def test_happy_path_routes_all_seven_media_types_correctly(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    # V3 (DIAGRAM) no longer calls a provider (Phase 25) -- only V1
    # (GENERATED_STILL) does.
    assert provider.call_count == 1
    assert result.provider_call_count == 1
    assert result.rendered_asset_count == 2
    assert result.reuse_requirement_count == 1
    assert result.canonical_asset_ready_count == 1
    assert result.external_requirement_count == 3

    rendered_beat_ids = {asset.beat_id for asset in result.manifest.assets}
    assert rendered_beat_ids == {"V1", "V3"}

    reuse_beat_ids = {
        req.beat_id for req in result.manifest.requirements if req.status.value == "REUSE_ONLY"
    }
    assert reuse_beat_ids == {"V6"}

    canonical_ready_beat_ids = {
        req.beat_id
        for req in result.manifest.requirements
        if req.status.value == "CANONICAL_ASSET_READY"
    }
    assert canonical_ready_beat_ids == {"V2"}

    external_beat_ids = {
        req.beat_id for req in result.manifest.requirements if req.status.value == "EXTERNAL_REQUIRED"
    }
    assert external_beat_ids == {"V4", "V5", "V7"}

    for asset in result.manifest.assets:
        written_path = store.root / asset.file_path
        assert written_path.exists()
        if asset.beat_id == "V1":  # provider-rendered
            assert written_path.read_bytes() == b"fake-png-bytes"
        else:  # V3 -- DiagramRenderer's own real PNG, not the provider fixture bytes
            assert asset.beat_id == "V3"
            assert written_path.read_bytes() != b"fake-png-bytes"

    stored = get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)
    assert stored == result.manifest

    project = get_project(engine, project_id)
    assert project.state == ProjectState.MVP_COMPLETE

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.SUCCESS
    assert render_runs[0].output_id == str(result.manifest.id)
    assert render_runs[0].input_ids == [str(script_plan.id), str(voice_plan.id), str(visual_plan.id)]


# ---------------------------------------------------------------------------
# Request ownership (spec section 44)
# ---------------------------------------------------------------------------


def test_request_fields_exactly_equal_visual_beat_fields(engine, tmp_path):
    beats = [
        _visual_beat(
            "V1",
            media_type="GENERATED_STILL",
            concept="A precise, specific concept",
            primary_focus="A precise, specific focus",
            secondary_elements=["crowd", "flag"],
            context_elements=["1940s newsreel style"],
            motion_intent="none",
            reuse_key=None,
        )
    ]
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(
        engine, beats=beats
    )
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer.run(VisualRendererInput(project_id=project_id))

    request = provider.received_requests[0]
    assert request.render_job_id == "V1_R1"
    assert request.beat_id == "V1"
    assert request.media_type.value == "GENERATED_STILL"
    assert request.concept == "A precise, specific concept"
    assert request.primary_focus == "A precise, specific focus"
    assert request.secondary_elements == ["crowd", "flag"]
    assert request.context_elements == ["1940s newsreel style"]
    assert request.motion_intent == "none"
    assert request.reuse_key is None
    assert request.output_format.value == "PNG"


# ---------------------------------------------------------------------------
# Call order (spec section 45)
# ---------------------------------------------------------------------------


def test_provider_call_order_follows_beat_order(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="ASSET_REUSE", reuse_key="k1"),
        _visual_beat("V2", media_type="GENERATED_STILL"),
        _visual_beat("V3", media_type="TI_STATE", ti_state="NEUTRAL"),
        _visual_beat("V4", media_type="DIAGRAM"),
    ]
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(
        engine, beats=beats
    )
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    # V4 (DIAGRAM) no longer calls a provider (Phase 25) -- only V2
    # (GENERATED_STILL) does.
    beat_ids_in_call_order = [req.beat_id for req in provider.received_requests]
    assert beat_ids_in_call_order == ["V2"]

    manifest_render_job_ids = [asset.render_job_id for asset in result.manifest.assets]
    assert manifest_render_job_ids == ["V2_R1", "V4_R1"]


# ---------------------------------------------------------------------------
# ASSET_REUSE (spec sections 9, 46)
# ---------------------------------------------------------------------------


def test_asset_reuse_calls_no_provider_and_uses_reuse_key(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="ASSET_REUSE", reuse_key="ti_hero_shot")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert len(result.manifest.requirements) == 1
    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "REUSE_ONLY"
    assert requirement.reference == "ti_hero_shot"


def test_asset_reuse_without_reuse_key_is_invalid(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="ASSET_REUSE", reuse_key=None)]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(InvalidVisualBeatError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# TI_STATE (Phase 19 sections 10, 47 -- superseded by Phase 23's
# deterministic canonical resolution/compositing; see
# tests/test_visual_renderer_ti_state_integration.py for the full Phase 23
# focused-test suite. These remaining tests here cover the basic routing
# shape only.)
# ---------------------------------------------------------------------------


def test_ti_state_standalone_calls_no_provider_and_resolves_canonical_asset(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS", reuse_key=None)]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "CANONICAL_ASSET_READY"
    assert requirement.resolved_ti_state is TiState.CURIOUS
    expected_asset = ti_compositor.retriever.get_asset(TiState.CURIOUS)
    assert requirement.reference == expected_asset.relative_path


def test_ti_state_no_longer_honors_reuse_key_override(engine, tmp_path):
    """Phase 23 intentionally removes the pre-canonical-asset "reuse_key
    overrides ti_state for TI_STATE beats" behavior: a real canonical
    asset now always exists for every TiState, so an arbitrary reuse_key
    placeholder is no longer a meaningful substitute."""
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS", reuse_key="ti_curious_v2")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "CANONICAL_ASSET_READY"
    assert requirement.reference != "ti_curious_v2"
    expected_asset = ti_compositor.retriever.get_asset(TiState.CURIOUS)
    assert requirement.reference == expected_asset.relative_path


def test_ti_state_without_compositor_configured_fails_clearly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)  # no ti_compositor

    with pytest.raises(TiCompositorNotConfiguredError):
        renderer.run(VisualRendererInput(project_id=project_id))
    assert provider.call_count == 0


def test_ti_state_composite_mode_without_background_fails_clearly(engine, tmp_path):
    # Phase 24: COMPOSITE now requires exactly one of background_path/
    # background_beat_id -- supplying neither fails at TiStateSource
    # construction itself (pydantic ValidationError), before a
    # VisualRendererInput or renderer.run() is ever reached.
    with pytest.raises(ValidationError):
        TiStateSource(mode=TiStateRenderMode.COMPOSITE)


# ---------------------------------------------------------------------------
# LIMITED_MOTION (spec sections 8, 29, 48)
# ---------------------------------------------------------------------------


def test_limited_motion_calls_no_provider_and_is_external_required(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="LIMITED_MOTION", motion_intent="slow zoom in")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "EXTERNAL_REQUIRED"
    assert requirement.media_type.value == "LIMITED_MOTION"
    assert "slow zoom in" in requirement.notes


# ---------------------------------------------------------------------------
# EVIDENCE_MEDIA (spec sections 8, 28, 49)
# ---------------------------------------------------------------------------


def test_evidence_media_calls_no_provider_and_preserves_evidence_ids(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="EVIDENCE_MEDIA", evidence_source_ids=["E001", "E002"])]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "EXTERNAL_REQUIRED"
    assert requirement.reference == "E001,E002"


# ---------------------------------------------------------------------------
# AI_HERO_VIDEO (spec sections 8, 30, 50)
# ---------------------------------------------------------------------------


def test_ai_hero_video_calls_no_provider_and_is_external_required(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="AI_HERO_VIDEO")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "EXTERNAL_REQUIRED"
    assert requirement.media_type.value == "AI_HERO_VIDEO"


# ---------------------------------------------------------------------------
# Stale VoicePlan / VisualPlan gates (spec sections 31, 58, 59)
# ---------------------------------------------------------------------------


def test_stale_voice_plan_blocks_before_any_side_effect(engine, tmp_path):
    project_id, script_plan_a, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)

    script_plan_b = _valid_script_plan(("L001", "L002", "L003", "L004"))
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan_b)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan_b.id)

    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(StaleVoicePlanError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []
    assert not any(
        run.module == "visual_renderer" for run in list_module_runs_for_project(engine, project_id)
    )
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)


def test_stale_visual_plan_blocks_before_any_side_effect(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan_a = _save_voice_plan(engine, project_id, script_plan.id)
    _save_visual_plan(engine, project_id, script_plan.id, voice_plan_a.id, _seven_beat_shortlist())

    # VoicePlan is regenerated (still fresh against ScriptPlan), but the
    # stored VisualPlan still references the OLD VoicePlan id.
    voice_plan_b = VoicePlan(
        script_plan_id=script_plan.id,
        chunks=[
            VoiceChunk(
                chunk_id="C002",
                line_ids=["L001", "L002"],
                voice_state="NEUTRAL",
                pace="NORMAL",
                energy="MEDIUM",
                take_count=1,
                music_state="BED",
            )
        ],
    )
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan_b)

    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(StaleVisualPlanError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []
    assert not any(
        run.module == "visual_renderer" for run in list_module_runs_for_project(engine, project_id)
    )
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)


# ---------------------------------------------------------------------------
# Provider retry (spec sections 17, 51, 52, 53)
# ---------------------------------------------------------------------------


def test_provider_retry_succeeds_within_bound(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([VisualProviderError("transient upstream error"), _visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(max_provider_retries=1), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 2
    assert result.provider_call_count == 2
    assert result.rendered_asset_count == 1


def test_provider_retry_exhaustion_propagates_and_marks_module_run_failed(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([VisualProviderError("down"), VisualProviderError("still down")])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(max_provider_retries=1), store)

    with pytest.raises(VisualProviderError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 2

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.FAILED

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)


# ---------------------------------------------------------------------------
# File-write failure (spec sections 36, 54, 55)
# ---------------------------------------------------------------------------


def test_write_failure_is_not_retried_against_the_provider(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL")]
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(
        engine, beats=beats
    )
    visuals_root = tmp_path / "visuals"
    blocking_path = visuals_root / str(project_id) / str(visual_plan.id) / "V1_R1.png"
    blocking_path.parent.mkdir(parents=True)
    blocking_path.mkdir()  # a directory already sits where the only output file must go

    # Only ONE response queued: if the renderer wrongly retried the
    # provider after the write failure, FakeVisualProvider would raise its
    # own "exhausted" VisualProviderError instead -- a different, wrong
    # exception.
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(visuals_root)
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(VisualWriteError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 1

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert render_runs[0].status == ModuleRunStatus.FAILED


def test_partial_write_failure_leaves_earlier_files_orphaned_and_no_manifest(engine, tmp_path):
    """Documents actual behavior: a mid-run failure does not roll back
    already-written files -- only the manifest artifact is withheld.
    V2 (DIAGRAM) is the one sabotaged to fail here, so the expected
    exception is DiagramRenderError (Phase 25) rather than the provider
    path's VisualWriteError -- DiagramRenderer writes its own output file
    directly rather than going through VisualFileStore.write()."""
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
    ]
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(
        engine, beats=beats
    )
    visuals_root = tmp_path / "visuals"
    blocking_path = visuals_root / str(project_id) / str(visual_plan.id) / "V2_R1.png"
    blocking_path.parent.mkdir(parents=True)
    blocking_path.mkdir()  # only the second beat's write fails

    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(visuals_root)
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(DiagramRenderError):
        renderer.run(VisualRendererInput(project_id=project_id))

    orphaned = visuals_root / str(project_id) / str(visual_plan.id) / "V1_R1.png"
    assert orphaned.exists()  # orphaned, not cleaned up
    assert orphaned.read_bytes() == b"fake-png-bytes"

    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)


# ---------------------------------------------------------------------------
# Manifest integrity wiring (defense in depth)
# ---------------------------------------------------------------------------


def test_manifest_integrity_failure_blocks_persistence(engine, tmp_path, monkeypatch):
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)
    provider = FakeVisualProvider([_visual_response(), _visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    import app.renderers.visual.renderer as renderer_module

    monkeypatch.setattr(
        renderer_module, "validate_visual_render_manifest", lambda *args, **kwargs: ["synthetic issue"]
    )

    with pytest.raises(VisualRenderManifestIntegrityError):
        renderer.run(VisualRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert render_runs[0].status == ModuleRunStatus.FAILED
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)


# ---------------------------------------------------------------------------
# Manifest persistence/retrieval and rerender/upsert semantics (spec section 61)
# ---------------------------------------------------------------------------


def test_rerender_overwrites_the_stored_manifest(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)
    provider = FakeVisualProvider([_visual_response() for _ in range(4)])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    first_result = renderer.run(VisualRendererInput(project_id=project_id))
    second_result = renderer.run(VisualRendererInput(project_id=project_id))

    assert first_result.manifest.id != second_result.manifest.id
    stored = get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)
    assert stored == second_result.manifest

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 2
    assert all(run.status == ModuleRunStatus.SUCCESS for run in render_runs)


# ---------------------------------------------------------------------------
# ScriptPlan / VoicePlan / VisualPlan immutability (spec section 60)
# ---------------------------------------------------------------------------


def test_rendering_never_mutates_script_voice_or_visual_plan(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)
    provider = FakeVisualProvider([_visual_response(), _visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer.run(VisualRendererInput(project_id=project_id))

    assert get_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan) == script_plan
    assert get_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, VoicePlan) == voice_plan
    assert get_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan) == visual_plan


# ---------------------------------------------------------------------------
# Bytes never persisted to SQLite (spec section 62)
# ---------------------------------------------------------------------------


def test_persisted_manifest_payload_never_contains_asset_bytes(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    marker = b"UNMISTAKABLE_ASSET_BYTES_MARKER"
    provider = FakeVisualProvider([_visual_response(asset_bytes=marker)])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer.run(VisualRendererInput(project_id=project_id))

    from sqlalchemy.orm import Session

    from app.storage.orm import ArtifactRow

    with Session(engine) as session:
        row = session.get(ArtifactRow, (str(project_id), VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE))
        assert marker.decode() not in row.payload_json


# ---------------------------------------------------------------------------
# Invalid start state / missing artifacts
# ---------------------------------------------------------------------------


def test_invalid_start_state_rejected(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)
    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.state = ProjectState.SCRIPT_REVIEW.value

    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(RendererStateError):
        renderer.run(VisualRendererInput(project_id=project_id))
    assert provider.call_count == 0


def test_missing_script_plan_artifact(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(engine)
    from sqlalchemy.orm import Session

    from app.storage.orm import ProjectRow

    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        row.script_plan_id = None

    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingScriptPlanArtifactError):
        renderer.run(VisualRendererInput(project_id=project_id))
    assert provider.call_count == 0


def test_missing_voice_plan_artifact(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    # No VoicePlan artifact saved at all.
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingVoicePlanArtifactError):
        renderer.run(VisualRendererInput(project_id=project_id))
    assert provider.call_count == 0


def test_missing_visual_plan_artifact(engine, tmp_path):
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    _save_voice_plan(engine, project_id, script_plan.id)
    # No VisualPlan artifact saved at all.
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingVisualPlanArtifactError):
        renderer.run(VisualRendererInput(project_id=project_id))
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# LLM-absence audit (spec section 63)
# ---------------------------------------------------------------------------


_LLM_IMPORT_RE = re.compile(r"^\s*(import\s+app\.llm\b|from\s+app\.llm\b)")


def test_visual_renderer_source_has_no_llm_import_statement():
    renderers_root = Path(__file__).resolve().parent.parent / "app" / "renderers"
    offending = []
    for path in sorted(renderers_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _LLM_IMPORT_RE.match(line):
                offending.append(f"{path}:{lineno}: {line.strip()}")
    assert offending == []


def test_visual_layer_source_has_no_llm_import_statement():
    visual_root = Path(__file__).resolve().parent.parent / "app" / "visual"
    offending = []
    for path in sorted(visual_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _LLM_IMPORT_RE.match(line):
                offending.append(f"{path}:{lineno}: {line.strip()}")
    assert offending == []


def test_importing_visual_renderer_never_loads_app_llm():
    """Runs in a fresh subprocess -- within this test process, an earlier
    test module may have already imported app.llm, which would make an
    in-process sys.modules check meaningless."""
    project_root = Path(__file__).resolve().parent.parent
    script = (
        "import sys\n"
        "import app.renderers.visual\n"
        "loaded = [name for name in sys.modules "
        "if name == 'app.llm' or name.startswith('app.llm.')]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
