"""The provider-independent visual-render interface. Concrete providers
implement only this. No vendor SDK in this module."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.visual.models import VisualRenderRequest, VisualRenderResponse


@runtime_checkable
class VisualProvider(Protocol):
    def render(self, request: VisualRenderRequest) -> VisualRenderResponse:
        ...
