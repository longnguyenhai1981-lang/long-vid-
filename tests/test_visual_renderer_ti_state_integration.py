"""Phase 23 focused tests: VisualRenderer <-> TiCompositor integration.

Reuses tests/test_visual_renderer.py's project-graph builders (a full
MVP_COMPLETE project with ScriptPlan/VoicePlan/VisualPlan already saved)
rather than re-deriving ~150 lines of unrelated fixture setup -- this file
only adds the TI_STATE-specific scenarios Phase 23 introduces. See that
file for the full non-TI_STATE routing suite (GENERATED_STILL/DIAGRAM/
ASSET_REUSE/LIMITED_MOTION/EVIDENCE_MEDIA/AI_HERO_VIDEO), left completely
unmodified in behavior by this phase.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import ModuleRunStatus, VisualTiState
from app.models.ti_assets import TiState
from app.renderers.visual.errors import TiCompositorNotConfiguredError
from app.renderers.visual.models import TiStateRenderMode, TiStateSource, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.storage.errors import TiAssetSetNotFoundError
from app.storage.module_runs import list_module_runs_for_project
from app.ti_assets.errors import TiAssetMissingFileError
from app.ti_assets.retriever import SqliteTiAssetRetriever
from app.ti_assets.storage import TiAssetFileStore
from app.ti_assets.visual_state_mapping import to_ti_state
from app.ti_compositor.compositor import TiCompositor
from app.ti_compositor.models import TiAnchor, TiPlacement, TiScalePolicy
from app.visual.fake import FakeVisualProvider
from app.visual.storage import VisualFileStore
from tests._ti_compositor_helpers import write_background, write_full_ti_asset_set
from tests.test_visual_renderer import (
    _create_project_ready_for_visual_rendering,
    _settings,
    _visual_beat,
)

BACKGROUND_COLOR = (10, 20, 200)


def _ti_compositor(engine, tmp_path, *, active: bool = True) -> TiCompositor:
    file_store = TiAssetFileStore(tmp_path / "ti_assets")
    if active:
        write_full_ti_asset_set(engine, file_store)
    return TiCompositor(SqliteTiAssetRetriever(engine, file_store))


def _background(tmp_path, name="bg.png") -> str:
    path = write_background(tmp_path / name, 400, 300, BACKGROUND_COLOR, "PNG")
    return str(path)


# ---------------------------------------------------------------------------
# Standalone mode: canonical asset resolution, VisualTiState mapping, zero
# provider calls
# ---------------------------------------------------------------------------


def test_standalone_resolves_active_canonical_asset(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="PANIC")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    requirement = result.manifest.requirements[0]
    assert requirement.status.value == "CANONICAL_ASSET_READY"
    expected_asset = ti_compositor.retriever.get_asset(TiState.PANIC)
    assert requirement.reference == expected_asset.relative_path
    assert result.manifest.assets == []


@pytest.mark.parametrize(
    "visual_ti_state,expected_ti_state",
    [
        (VisualTiState.NEUTRAL, TiState.NEUTRAL),
        (VisualTiState.CONFUSED, TiState.CURIOUS),
        (VisualTiState.SURPRISED, TiState.EXCITED),
        (VisualTiState.SMUG, TiState.SKEPTICAL),
    ],
)
def test_standalone_uses_existing_visual_ti_state_mapping(
    engine, tmp_path, visual_ti_state, expected_ti_state
):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state=visual_ti_state.value)]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    requirement = result.manifest.requirements[0]
    assert requirement.resolved_ti_state is expected_ti_state
    assert to_ti_state(visual_ti_state) is expected_ti_state  # sanity: same mapping renderer used
    expected_asset = ti_compositor.retriever.get_asset(expected_ti_state)
    assert requirement.reference == expected_asset.relative_path


def test_standalone_mode_makes_zero_provider_calls(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="EXCITED")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert result.provider_call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []  # no file written either


# ---------------------------------------------------------------------------
# Composite mode: invokes TiCompositor, registers a real asset in the manifest
# ---------------------------------------------------------------------------


def test_composite_mode_invokes_ti_compositor_and_calls_zero_providers(engine, tmp_path):
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
    assert result.provider_call_count == 0
    assert result.manifest.requirements == []
    assert len(result.manifest.assets) == 1


def test_composite_output_registered_in_manifest_as_real_file(engine, tmp_path):
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

    asset = result.manifest.assets[0]
    assert asset.beat_id == "V1"
    assert asset.media_type.value == "TI_STATE"
    assert asset.resolved_ti_state is TiState.CURIOUS
    assert asset.file_path.endswith(".png")
    assert asset.width == 400  # background width -- output dims == background dims
    assert asset.height == 300

    written_path = store.root / asset.file_path
    assert written_path.is_file()


def test_composite_mode_honors_explicit_placement_override(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    background_path = _background(tmp_path)
    placement = TiPlacement(anchor=TiAnchor.CENTER, scale=TiScalePolicy(relative_height=0.1))
    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V1": TiStateSource(
                mode=TiStateRenderMode.COMPOSITE, background_path=background_path, placement=placement
            )
        },
    )

    result = renderer.run(renderer_input)
    assert len(result.manifest.assets) == 1  # ran without error using the override


# ---------------------------------------------------------------------------
# Error behavior: no AI fallback, fail clearly
# ---------------------------------------------------------------------------


def test_missing_background_in_composite_mode_fails_before_compositing(engine, tmp_path):
    # Phase 24: COMPOSITE now requires exactly one of background_path/
    # background_beat_id -- supplying neither fails at TiStateSource
    # construction (pydantic ValidationError), before a renderer even runs.
    with pytest.raises(ValidationError):
        TiStateSource(mode=TiStateRenderMode.COMPOSITE)


def test_missing_active_canonical_set_fails_explicitly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path, active=False)  # no TiAssetSet stored at all
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    with pytest.raises(TiAssetSetNotFoundError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0


def test_missing_canonical_file_fails_explicitly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="PANIC")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")

    file_store = TiAssetFileStore(tmp_path / "ti_assets")
    asset_set = write_full_ti_asset_set(engine, file_store)
    panic_asset = next(a for a in asset_set.assets if a.state is TiState.PANIC)
    file_store.resolve(panic_asset.relative_path).unlink()  # metadata says it exists; file does not
    ti_compositor = TiCompositor(SqliteTiAssetRetriever(engine, file_store))
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    with pytest.raises(TiAssetMissingFileError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0


def test_ti_compositor_not_configured_fails_before_any_provider_call(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="PANIC")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)  # ti_compositor=None

    with pytest.raises(TiCompositorNotConfiguredError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0


def test_no_ai_fallback_in_either_mode(engine, tmp_path):
    """Belt-and-suspenders: neither STANDALONE nor COMPOSITE mode ever
    reaches the injected VisualProvider, even though a provider with
    queued responses IS available -- if either mode silently fell back to
    AI generation, FakeVisualProvider.call_count would be nonzero."""
    beats = [
        _visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="PANIC"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])  # any call at all raises "exhausted"
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    background_path = _background(tmp_path)
    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_path=background_path)
        },
    )

    result = renderer.run(renderer_input)  # would raise VisualProviderError if a fallback call happened

    assert provider.call_count == 0
    assert result.canonical_asset_ready_count == 1  # V1 standalone
    assert result.rendered_asset_count == 1  # V2 composited


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_standalone_reference_is_deterministic_across_reruns(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="SKEPTICAL")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    first = renderer.run(VisualRendererInput(project_id=project_id))
    second = renderer.run(VisualRendererInput(project_id=project_id))

    assert first.manifest.requirements[0].reference == second.manifest.requirements[0].reference


def test_composite_output_bytes_are_deterministic_across_reruns(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="SKEPTICAL")]
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

    first = renderer.run(renderer_input)
    first_bytes = (store.root / first.manifest.assets[0].file_path).read_bytes()
    second = renderer.run(renderer_input)
    second_bytes = (store.root / second.manifest.assets[0].file_path).read_bytes()

    assert first.manifest.assets[0].file_path == second.manifest.assets[0].file_path
    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# ModuleRun / freshness behavior preserved
# ---------------------------------------------------------------------------


def test_module_run_recorded_success_for_composite_mode(engine, tmp_path):
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

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.SUCCESS
    assert render_runs[0].output_id == str(result.manifest.id)


def test_module_run_recorded_failed_when_ti_state_fails(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="TI_STATE", ti_state="CURIOUS")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)  # no compositor -> will fail

    with pytest.raises(TiCompositorNotConfiguredError):
        renderer.run(VisualRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.FAILED
