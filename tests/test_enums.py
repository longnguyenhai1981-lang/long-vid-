from __future__ import annotations

import pytest

from app.models.common import ClaimStatus, GateStatus, PrimaryPayoff, ProjectState


def test_valid_enum_values_parse():
    assert PrimaryPayoff("DISCOVERY") is PrimaryPayoff.DISCOVERY
    assert GateStatus("PASS") is GateStatus.PASS
    assert ClaimStatus("PROHIBITED") is ClaimStatus.PROHIBITED
    assert ProjectState("MVP_COMPLETE") is ProjectState.MVP_COMPLETE


def test_invalid_enum_values_reject():
    with pytest.raises(ValueError):
        PrimaryPayoff("NOT_A_PAYOFF")
    with pytest.raises(ValueError):
        GateStatus("MAYBE")
    with pytest.raises(ValueError):
        ProjectState("UNKNOWN_STATE")
