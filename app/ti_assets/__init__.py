"""Canonical Tí brand-asset system (Phase 21).

Deterministic storage, validation, and retrieval for a small, human-curated
set of canonical Tí image files -- the alternative to ever generating Tí from
a prompt. See app/models/ti_assets.py for the typed contracts, and this
package's retriever.py for the boundary a future compositor/VisualRenderer
integration will consume. Nothing here calls a VisualProvider, an LLM, or
performs any scene compositing -- that is explicitly out of scope for
Phase 21.
"""

from __future__ import annotations
