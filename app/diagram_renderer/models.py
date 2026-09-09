"""Typed result contract for DiagramRenderer (Phase 25).

Plain, dependency-free -- mirrors app/ti_compositor/models.py's
TiCompositeResult in spirit: enough detail for a caller/test to assert on
without re-opening the output image.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator

from app.models.common import MotilyModel


class DiagramRenderResult(MotilyModel):
    output_path: Path
    width: int
    height: int

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramRenderResult":
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be > 0")
        return self
