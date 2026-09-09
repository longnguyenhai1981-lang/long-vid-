"""Deterministic VisualTiState -> TiState mapping (Phase 21.1).

VisualTiState (app/models/common.py, Phase 14) is visual/narrative-planning
semantics -- what mood a generated still's prompt should convey.  TiState
(app/models/ti_assets.py, Phase 21) is canonical-brand-asset identity --
which fixed, human-curated image file a future compositor reuses. They stay
two separate enums on purpose (see docs/TECHNICAL_SPEC_v0.1.md's Phase 21
"open decision" and this phase's own record): related concepts, not the
same one. This module is the single, explicit, exhaustive mapping between
them -- no fuzzy matching, no string-name coercion, and no silent default
to NEUTRAL. An unmapped VisualTiState is a hard error, not a fallback.

TiState.SERIOUS and TiState.LOW_ENERGY have no VisualTiState counterpart in
this mapping -- they are canonical assets a future compositor/editor may
select directly (e.g. from an authored beat's explicit state choice, not
derived from a VisualTiState), not values this mapping ever produces from
CONFUSED/SURPRISED/SMUG or any other VisualTiState. This is intentional,
not an omission: nothing in VisualTiState's 9-member vocabulary expresses
"serious" or "low energy" today.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from app.models.common import VisualTiState
from app.models.ti_assets import TiState

VISUAL_TI_STATE_TO_TI_STATE: Mapping[VisualTiState, TiState] = MappingProxyType(
    {
        VisualTiState.NEUTRAL: TiState.NEUTRAL,
        VisualTiState.CURIOUS: TiState.CURIOUS,
        VisualTiState.SKEPTICAL: TiState.SKEPTICAL,
        VisualTiState.EXCITED: TiState.EXCITED,
        VisualTiState.DEADPAN: TiState.DEADPAN,
        VisualTiState.PANIC: TiState.PANIC,
        # Canonical decisions for the three VisualTiState values with no
        # like-for-like TiState counterpart:
        VisualTiState.CONFUSED: TiState.CURIOUS,
        VisualTiState.SURPRISED: TiState.EXCITED,
        VisualTiState.SMUG: TiState.SKEPTICAL,
    }
)

if VISUAL_TI_STATE_TO_TI_STATE.keys() != set(VisualTiState):
    _missing = set(VisualTiState) - VISUAL_TI_STATE_TO_TI_STATE.keys()
    raise RuntimeError(
        "VISUAL_TI_STATE_TO_TI_STATE is incomplete, missing VisualTiState "
        "member(s): " + ", ".join(sorted(m.value for m in _missing))
    )


def to_ti_state(visual_state: VisualTiState) -> TiState:
    """The one deterministic lookup this mapping exists for. Raises
    ValueError -- never returns a default -- if visual_state is somehow not
    a mapped VisualTiState member (unreachable for a real VisualTiState
    value given the completeness check above, but never silently
    swallowed)."""
    try:
        return VISUAL_TI_STATE_TO_TI_STATE[visual_state]
    except KeyError as exc:
        raise ValueError(
            f"No canonical TiState mapping defined for VisualTiState {visual_state!r}"
        ) from exc
