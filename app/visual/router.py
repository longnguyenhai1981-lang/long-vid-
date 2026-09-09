"""A tiny composite VisualProvider that dispatches to a different concrete
provider per VisualMediaType -- the smallest clean production-routing
mechanism (Phase 20), deliberately not a generic mega-router/plugin
framework.

VisualRenderer itself is never modified to support this: MediaTypeVisualProvider
satisfies VisualProvider structurally, exactly like any single concrete
provider would, so it is simply handed to VisualRenderer as its
visual_provider argument. There is no fallback guessing and no LLM
involved in the dispatch decision -- a request's media_type either has a
configured provider or it doesn't.
"""

from __future__ import annotations

from app.models.common import VisualMediaType
from app.visual.errors import VisualProviderUnavailableError
from app.visual.models import VisualRenderRequest, VisualRenderResponse
from app.visual.provider import VisualProvider


class MediaTypeVisualProvider:
    def __init__(self, providers_by_media_type: dict[VisualMediaType, VisualProvider]):
        self._providers_by_media_type = dict(providers_by_media_type)

    def render(self, request: VisualRenderRequest) -> VisualRenderResponse:
        provider = self._providers_by_media_type.get(request.media_type)
        if provider is None:
            raise VisualProviderUnavailableError(
                f"No VisualProvider is configured for media_type "
                f"{request.media_type.value!r}"
            )
        return provider.render(request)
