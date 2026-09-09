"""Phase 24 focused tests: beat-to-beat visual composition.

TiStateSource.background_beat_id (app/renderers/visual/models.py) lets a
TI_STATE COMPOSITE beat use another VisualBeat's own RenderedVisualAsset,
produced earlier in the same render pass, as its background -- instead of
an explicit background_path (Phase 23, left completely unchanged). This
file covers only the Phase 24-specific contract, dependency-graph,
resolution, provider-call, manifest, and lifecycle scenarios. See
tests/test_visual_renderer.py and
tests/test_visual_renderer_ti_state_integration.py for the full
non-Phase-24 routing / PATH-mode suites, both left unmodified in behavior
by this phase.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.common import ModuleRunStatus
from app.models.visual import VisualPlan
from app.renderers.visual.errors import (
    BackgroundBeatAssetMissingError,
    BackgroundBeatCycleError,
    BackgroundBeatNotRenderedError,
    SelfReferentialBackgroundBeatError,
    UnknownBackgroundBeatError,
)
from app.renderers.visual.models import TiStateRenderMode, TiStateSource, VisualRendererInput
from app.renderers.visual.renderer import (
    VisualRenderer,
    _build_background_dependency_edges,
    _topological_beat_order,
)
from app.storage.module_runs import list_module_runs_for_project
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderResponse
from app.visual.storage import VisualFileStore
from tests._ti_compositor_helpers import make_background_bytes
from tests.test_visual_renderer import (
    _create_project_ready_for_visual_rendering,
    _settings,
    _visual_beat,
)
from tests.test_visual_renderer_ti_state_integration import _background, _ti_compositor


def _real_still_response(width=400, height=300, color=(10, 20, 200)) -> VisualRenderResponse:
    """A GENERATED_STILL/DIAGRAM provider response carrying a REAL,
    Pillow-decodable PNG -- unlike test_visual_renderer.py's placeholder
    b"fake-png-bytes", this file's tests need the resulting file to be
    usable as a TiCompositor background."""
    return VisualRenderResponse(
        asset_bytes=make_background_bytes(width, height, color, "PNG"),
        provider="fake-visual",
        output_format="PNG",
        width=width,
        height=height,
    )


def _minimal_visual_plan(beats) -> VisualPlan:
    """A bare VisualPlan for unit-testing the dependency-graph helpers
    directly, without a full project/database fixture."""
    return VisualPlan(script_plan_id=uuid4(), voice_plan_id=uuid4(), beats=beats)


# ---------------------------------------------------------------------------
# Contract: TiStateSource background source validation
# ---------------------------------------------------------------------------


def test_standalone_rejects_background_path():
    with pytest.raises(ValidationError):
        TiStateSource(mode=TiStateRenderMode.STANDALONE, background_path="bg.png")


def test_standalone_rejects_background_beat_id():
    with pytest.raises(ValidationError):
        TiStateSource(mode=TiStateRenderMode.STANDALONE, background_beat_id="V1")


def test_composite_requires_exactly_one_source_neither_supplied():
    with pytest.raises(ValidationError):
        TiStateSource(mode=TiStateRenderMode.COMPOSITE)


def test_composite_rejects_both_path_and_beat_id():
    with pytest.raises(ValidationError):
        TiStateSource(
            mode=TiStateRenderMode.COMPOSITE, background_path="bg.png", background_beat_id="V1"
        )


def test_composite_accepts_background_path_alone():
    source = TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_path="bg.png")
    assert source.background_beat_id is None


def test_composite_accepts_background_beat_id_alone():
    source = TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
    assert source.background_path is None


# ---------------------------------------------------------------------------
# Dependency planning: pure unit tests over the graph helpers
# ---------------------------------------------------------------------------


def test_generated_still_to_ti_state_dependency_orders_background_first():
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="GENERATED_STILL"),
    ]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V2")
        },
    )
    plan = _minimal_visual_plan(beats)

    edges = _build_background_dependency_edges(plan, renderer_input)
    order = _topological_beat_order(beats, edges)

    assert order.index("V2") < order.index("V1")


def test_referenced_beat_may_appear_later_in_authored_plan():
    # V1 (TI_STATE, depends on V2) authored BEFORE V2 (its background).
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="GENERATED_STILL"),
    ]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V2")
        },
    )
    plan = _minimal_visual_plan(beats)

    edges = _build_background_dependency_edges(plan, renderer_input)
    order = _topological_beat_order(beats, edges)

    assert order == ["V2", "V1"]


def test_independent_beats_preserve_authored_order():
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _visual_beat("V3", media_type="ASSET_REUSE", reuse_key="k"),
    ]
    plan = _minimal_visual_plan(beats)
    renderer_input = VisualRendererInput(project_id=uuid4())

    edges = _build_background_dependency_edges(plan, renderer_input)
    order = _topological_beat_order(beats, edges)

    assert order == ["V1", "V2", "V3"]


def test_stable_deterministic_topological_order_minimal_reorder():
    # Authored order [C, B, A]; C depends on B. The only forced change is
    # B moving ahead of C -- A, unrelated to the dependency, should not be
    # shuffled arbitrarily by tie-breaking.
    beats = [
        _visual_beat("C", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("B", media_type="GENERATED_STILL"),
        _visual_beat("A", media_type="GENERATED_STILL"),
    ]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "C": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="B")
        },
    )
    plan = _minimal_visual_plan(beats)

    edges = _build_background_dependency_edges(plan, renderer_input)
    order = _topological_beat_order(beats, edges)

    assert order == ["B", "C", "A"]

    # Determinism: repeated computation from the same inputs is identical.
    assert _topological_beat_order(beats, edges) == order


def test_self_reference_fails():
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )
    plan = _minimal_visual_plan(beats)

    with pytest.raises(SelfReferentialBackgroundBeatError):
        _build_background_dependency_edges(plan, renderer_input)


def test_unknown_background_beat_fails():
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V99")
        },
    )
    plan = _minimal_visual_plan(beats)

    with pytest.raises(UnknownBackgroundBeatError):
        _build_background_dependency_edges(plan, renderer_input)


def test_direct_two_beat_cycle_fails():
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="NEUTRAL"),
    ]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V2"),
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1"),
        },
    )
    plan = _minimal_visual_plan(beats)

    edges = _build_background_dependency_edges(plan, renderer_input)
    with pytest.raises(BackgroundBeatCycleError):
        _topological_beat_order(beats, edges)


def test_multi_beat_cycle_fails():
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="NEUTRAL"),
        _visual_beat("V3", media_type="TI_STATE", ti_state="EXCITED"),
    ]
    renderer_input = VisualRendererInput(
        project_id=uuid4(),
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V2"),
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V3"),
            "V3": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1"),
        },
    )
    plan = _minimal_visual_plan(beats)

    edges = _build_background_dependency_edges(plan, renderer_input)
    with pytest.raises(BackgroundBeatCycleError):
        _topological_beat_order(beats, edges)


# ---------------------------------------------------------------------------
# Resolution: full VisualRenderer.run() pipeline
# ---------------------------------------------------------------------------


def test_background_beat_id_resolves_to_rendered_asset(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    result = renderer.run(renderer_input)

    assert provider.call_count == 1  # only V1
    assert result.provider_call_count == 1

    v1_asset = next(a for a in result.manifest.assets if a.beat_id == "V1")
    v2_asset = next(a for a in result.manifest.assets if a.beat_id == "V2")
    assert v2_asset.media_type.value == "TI_STATE"
    assert v2_asset.width == 400  # V1's background dimensions
    assert v2_asset.height == 300
    assert (store.root / v1_asset.file_path).is_file()
    assert (store.root / v2_asset.file_path).is_file()


def test_referenced_requirement_instead_of_asset_fails(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="ASSET_REUSE", reuse_key="k"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    with pytest.raises(BackgroundBeatNotRenderedError):
        renderer.run(renderer_input)

    assert provider.call_count == 0


def test_missing_rendered_file_fails(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    # Sabotage: delete V1's rendered file the instant after it would have
    # been written, by monkeypatching the store's write to unlink right
    # after -- simpler: write is real; we delete the file straight after
    # the renderer would have produced it. Since we cannot hook mid-run
    # cleanly here, we instead pre-compute where V1's file will land and
    # remove it right after the run would create it by wrapping write.
    original_write = store.write

    def _write_then_delete_v1(relative_path, data):
        result = original_write(relative_path, data)
        if relative_path.endswith("V1_R1.png"):
            result.unlink()
        return result

    store.write = _write_then_delete_v1  # type: ignore[method-assign]

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    with pytest.raises(BackgroundBeatAssetMissingError):
        renderer.run(renderer_input)


def test_ti_state_composite_may_consume_another_ti_state_composite_output(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="PANIC"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    background_path = _background(tmp_path)
    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_path=background_path),
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1"),
        },
    )

    result = renderer.run(renderer_input)

    assert provider.call_count == 0
    assert len(result.manifest.assets) == 2
    v2_asset = next(a for a in result.manifest.assets if a.beat_id == "V2")
    assert (store.root / v2_asset.file_path).is_file()


# ---------------------------------------------------------------------------
# Compatibility: unaffected behaviors
# ---------------------------------------------------------------------------


def test_explicit_background_path_mode_unchanged(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    background_path = _background(tmp_path)
    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_path=background_path)
        },
    )

    result = renderer.run(renderer_input)

    assert provider.call_count == 0
    assert len(result.manifest.assets) == 1
    assert result.manifest.assets[0].beat_id == "V1"


def test_standalone_mode_unchanged(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="PANIC")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert result.manifest.assets == []
    assert result.manifest.requirements[0].status.value == "CANONICAL_ASSET_READY"


def test_asset_reuse_unchanged(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="ASSET_REUSE", reuse_key="ti_hero_shot")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert result.manifest.requirements[0].reference == "ti_hero_shot"


def test_non_ti_state_provider_routing_unchanged(engine, tmp_path):
    # DIAGRAM's OWN routing changed in Phase 25 (no longer calls a
    # provider at all -- see tests/test_visual_renderer_diagram_integration.py)
    # but this test's point stands: GENERATED_STILL's provider routing is
    # completely unaffected by either Phase 24 or Phase 25.
    beats = [_visual_beat("V1", media_type="GENERATED_STILL"), _visual_beat("V2", media_type="DIAGRAM")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 1
    assert {a.beat_id for a in result.manifest.assets} == {"V1", "V2"}


# ---------------------------------------------------------------------------
# Provider-call behavior
# ---------------------------------------------------------------------------


def test_generated_background_provider_call_counted_normally_dependent_adds_zero(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    result = renderer.run(renderer_input)

    assert provider.call_count == 1
    assert result.provider_call_count == 1
    assert len(provider.received_requests) == 1
    assert provider.received_requests[0].beat_id == "V1"


def test_v1_renders_before_v2_despite_authored_order_and_zero_extra_calls(engine, tmp_path):
    # V2 (dependent TI_STATE) authored BEFORE V1 (its background).
    beats = [
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V1", media_type="GENERATED_STILL"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    result = renderer.run(renderer_input)

    assert provider.call_count == 1
    render_job_order = [asset.beat_id for asset in result.manifest.assets]
    assert render_job_order.index("V1") < render_job_order.index("V2")


# ---------------------------------------------------------------------------
# Manifest behavior
# ---------------------------------------------------------------------------


def test_manifest_preserves_both_source_and_composite_assets(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    result = renderer.run(renderer_input)

    beat_ids = {a.beat_id for a in result.manifest.assets}
    assert beat_ids == {"V1", "V2"}
    file_paths = [a.file_path for a in result.manifest.assets]
    assert len(file_paths) == len(set(file_paths))  # deterministic, distinct paths


def test_manifest_paths_deterministic_across_reruns(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response(), _real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    first = renderer.run(renderer_input)
    second = renderer.run(renderer_input)

    first_paths = sorted(a.file_path for a in first.manifest.assets)
    second_paths = sorted(a.file_path for a in second.manifest.assets)
    assert first_paths == second_paths


# ---------------------------------------------------------------------------
# Lifecycle: ModuleRun / freshness / determinism
# ---------------------------------------------------------------------------


def test_module_run_success_preserved_for_dependent_composition(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    result = renderer.run(renderer_input)

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.SUCCESS
    assert render_runs[0].output_id == str(result.manifest.id)


def test_module_run_failed_preserved_on_cycle_error(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="NEUTRAL"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V1": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V2"),
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1"),
        },
    )

    with pytest.raises(BackgroundBeatCycleError):
        renderer.run(renderer_input)

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.FAILED
    assert provider.call_count == 0
