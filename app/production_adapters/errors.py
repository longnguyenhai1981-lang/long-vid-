"""Domain errors for app/production_adapters/ (Phase 34 requirement #39).

Narrow, adapter-plumbing errors only -- an underlying engine's own
exception (e.g. StaleVoicePlanError, FeasibilityNotPassedError) is never
re-wrapped; it propagates unchanged so ProductionRunner records the
original, useful message. These three cover situations the ENGINE itself
has no way to express because they are about the adapter/orchestrator
integration layer, not the engine's own business rules.
"""

from __future__ import annotations


class ProductionAdapterError(Exception):
    """Base class for all app/production_adapters/ errors."""


class ProductionInputMissingError(ProductionAdapterError):
    """Raised by the IDEA adapter's execute() when no IdeaCandidate exists
    yet AND ExecutionContext.initial_idea_input is None -- there is no
    sensible default discovery_mode/seed to fabricate, so the runner must
    never hallucinate the initial brief (requirement #7)."""


class UpstreamArtifactResolutionError(ProductionAdapterError):
    """Raised when an adapter's is_fresh()/execute() cannot resolve an
    upstream artifact it needs to interpret freshness or build engine
    input, even though the runner's own topological ordering should have
    guaranteed that artifact already exists this run. Signals an
    adapter/graph wiring bug, never an ordinary missing-artifact outcome
    (those are BLOCKED_DEPENDENCY, not exceptions)."""


class AdapterConfigurationError(ProductionAdapterError):
    """Raised when an adapter's execute() is called without the
    ExecutionContext dependencies it needs to even construct its
    underlying engine (e.g. llm_provider/llm_settings/global_config
    unset). A caller/test wiring bug, never a per-project outcome."""
