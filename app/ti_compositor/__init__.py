"""Deterministic Tí scene compositor (Phase 22).

Composites the active canonical Tí asset (see app/ti_assets/) onto an
existing background image using fixed, rule-based placement/scale --
no computer-vision placement, no saliency model, no LLM, and no image
generation for Tí by any provider. TiCompositor is a new, separate
architecture boundary: it depends on TiAssetRetriever (Phase 21) the same
way app/renderers/visual/renderer.py depends on VisualProvider, but it is
NOT wired into VisualRenderer, app/visual/router.py, or either concrete
VisualProvider (CloudflareImageProvider/GeminiImageProvider) -- that
integration is left to a future phase. See app/ti_compositor/compositor.py
for the one concrete implementation and app/ti_compositor/models.py for
the request/result/placement contracts.
"""

from __future__ import annotations
