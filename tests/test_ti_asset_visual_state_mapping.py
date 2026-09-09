from __future__ import annotations

import pytest

from app.models.common import VisualTiState
from app.models.ti_assets import TiState
from app.ti_assets.visual_state_mapping import VISUAL_TI_STATE_TO_TI_STATE, to_ti_state

EXPECTED = {
    VisualTiState.NEUTRAL: TiState.NEUTRAL,
    VisualTiState.CURIOUS: TiState.CURIOUS,
    VisualTiState.SKEPTICAL: TiState.SKEPTICAL,
    VisualTiState.EXCITED: TiState.EXCITED,
    VisualTiState.DEADPAN: TiState.DEADPAN,
    VisualTiState.PANIC: TiState.PANIC,
    VisualTiState.CONFUSED: TiState.CURIOUS,
    VisualTiState.SURPRISED: TiState.EXCITED,
    VisualTiState.SMUG: TiState.SKEPTICAL,
}


def test_every_visual_ti_state_member_has_exactly_one_mapping():
    assert set(VISUAL_TI_STATE_TO_TI_STATE.keys()) == set(VisualTiState)
    assert len(VISUAL_TI_STATE_TO_TI_STATE) == len(VisualTiState)


@pytest.mark.parametrize("visual_state,expected", list(EXPECTED.items()))
def test_required_mapping_table(visual_state, expected):
    assert to_ti_state(visual_state) is expected


def test_mapping_matches_full_expected_table_exactly():
    assert dict(VISUAL_TI_STATE_TO_TI_STATE) == EXPECTED


def test_mapping_is_deterministic_across_repeated_calls():
    for visual_state in VisualTiState:
        first = to_ti_state(visual_state)
        second = to_ti_state(visual_state)
        assert first is second


def test_mapping_does_not_mutate_visual_ti_state_enum():
    before = list(VisualTiState)
    for visual_state in VisualTiState:
        to_ti_state(visual_state)
    after = list(VisualTiState)
    assert before == after


def test_mapping_does_not_mutate_ti_state_enum():
    before = list(TiState)
    for visual_state in VisualTiState:
        to_ti_state(visual_state)
    after = list(TiState)
    assert before == after


def test_mapping_table_is_read_only():
    with pytest.raises(TypeError):
        VISUAL_TI_STATE_TO_TI_STATE[VisualTiState.NEUTRAL] = TiState.PANIC  # type: ignore[index]


def test_serious_and_low_energy_have_no_visual_ti_state_source():
    mapped_targets = set(VISUAL_TI_STATE_TO_TI_STATE.values())
    assert TiState.SERIOUS not in mapped_targets
    assert TiState.LOW_ENERGY not in mapped_targets


def test_ambiguous_states_do_not_silently_default_to_neutral():
    """CONFUSED/SURPRISED/SMUG have no like-for-like TiState -- confirm each
    resolves to its documented canonical decision, not a NEUTRAL fallback."""
    assert to_ti_state(VisualTiState.CONFUSED) is TiState.CURIOUS
    assert to_ti_state(VisualTiState.SURPRISED) is TiState.EXCITED
    assert to_ti_state(VisualTiState.SMUG) is TiState.SKEPTICAL


def test_to_ti_state_raises_for_unmapped_value_no_silent_fallback():
    class NotAVisualTiState:
        pass

    with pytest.raises(ValueError):
        to_ti_state(NotAVisualTiState())  # type: ignore[arg-type]
