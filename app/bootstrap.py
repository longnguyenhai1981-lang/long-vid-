"""Phase 35 requirement #8: the one explicit application composition
root. It is the only place that constructs the database engine, the
production graph, the full adapter registry, and the ProductionService
that wraps them -- no CLI command handler builds any of these itself.

Real-mode limitation (documented, not papered over): this codebase has
never implemented a concrete, network-calling `LLMProvider`
(app/llm/provider.py is a Protocol; app/llm/fake.py is the only
implementation) -- only the audio/visual TTS and image providers have
real Gemini/Cloudflare backends. `build_execution_context` therefore
leaves `llm_provider`/`llm_settings`/`tts_provider`/`visual_provider`
unset by default; a real `motily produce` run will correctly reach
`FAILED`/`AdapterConfigurationError` at IDEA rather than silently doing
nothing or crashing with a confusing stack trace. Tests and the
evaluation script inject fakes explicitly through
`ExecutionContext`'s own constructor -- this module never chooses a
fake for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from app.orchestration.graph import ProductionGraph
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.services.production_service import ProductionService
from app.storage.database import DEFAULT_DB_PATH, init_database


@dataclass(frozen=True)
class AppComposition:
    db_engine: Engine
    graph: ProductionGraph
    adapters: dict[str, object]
    service: ProductionService


def build_composition(db_path: Path | str = DEFAULT_DB_PATH) -> AppComposition:
    """The real-mode composition root: opens/creates the SQLite database
    at db_path, builds the full 19-node graph and adapter set (Phase 34),
    and wraps them in one ProductionService. Deterministic and
    side-effect-free beyond opening the database file -- no provider is
    constructed here (see module docstring)."""
    db_engine = init_database(db_path)
    graph = build_full_graph()
    adapters = build_full_adapters()
    service = ProductionService(db_engine, graph, adapters)
    return AppComposition(db_engine=db_engine, graph=graph, adapters=adapters, service=service)
