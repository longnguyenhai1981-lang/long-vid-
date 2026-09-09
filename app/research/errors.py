"""Research-retrieval-layer errors."""

from __future__ import annotations


class ResearchRetrieverError(Exception):
    """Raised when a research retrieval call itself fails (network/API/provider-side)."""
