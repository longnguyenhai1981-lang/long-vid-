"""TiCompositor: deterministic background+Tí image compositing (Phase 22).

Pillow is used here deliberately, unlike app/ti_assets/png_metadata.py's
explicit "Pillow rejected as unjustified" decision (Phase 21.1) -- that
module only ever needed to read a PNG's IHDR/tRNS header bytes, never to
decode pixel data. This module's job is fundamentally different: resizing
a raster image, alpha-compositing it onto another, and decoding JPEG
source bytes, none of which the stdlib does without hand-writing a
JPEG/PNG codec. Pillow is added as a project dependency (pyproject.toml)
for exactly this need. It is NOT used by, and must never be imported into,
app/ti_assets/png_metadata.py, app/visual/providers/cloudflare.py, or
app/visual/providers/gemini.py -- those stay exactly as they were.

Out-of-bounds policy (Phase 22 requirement #7 -- pick one, document it):
this compositor CLAMPS. TiScalePolicy already guarantees Tí's rendered
height never exceeds the background's height (0 < relative_height <= 1).
Given that, the requested anchor+margin+offset position is clamped so the
full rendered Tí image always stays within the background frame -- Tí is
nudged back in-frame rather than the request being rejected, and
TiCompositeResult.clamped reports whether clamping actually changed the
position. The one case this compositor refuses outright, via
TiCompositeBoundsError, is a rendered Tí width that exceeds the
background's width (possible when Tí's aspect ratio is wide relative to a
narrow background) -- there, no position, clamped or not, could ever fit
the full asset in-frame, so failing loudly is the only option that isn't
a silent crop.

Never generates Tí: the only image bytes this module ever reads for Tí
come from TiAssetRetriever.get_asset()/resolve_path(), i.e. the active,
human-curated TiAssetSet. No VisualProvider, no LLM, no network call.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.models.ti_assets import TiState
from app.ti_assets.retriever import TiAssetRetriever
from app.ti_assets.visual_state_mapping import to_ti_state
from app.ti_compositor.errors import (
    TiCompositeAssetError,
    TiCompositeBackgroundError,
    TiCompositeBoundsError,
    TiCompositeWriteError,
)
from app.ti_compositor.models import TiAnchor, TiCompositeRequest, TiCompositeResult

_SUPPORTED_BACKGROUND_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


class TiCompositor:
    """Composites the active canonical Tí asset onto an existing background
    image. Depends only on TiAssetRetriever -- never on VisualProvider, a
    concrete provider, or app/renderers/visual/renderer.py. As of Phase 23
    it IS consumed by app/renderers/visual/renderer.py -- through this
    same public surface, never by VisualRenderer reaching around it to
    instantiate its own TiAssetRetriever/SqliteTiAssetRetriever/
    TiAssetFileStore.
    """

    def __init__(self, retriever: TiAssetRetriever):
        self._retriever = retriever

    @property
    def retriever(self) -> TiAssetRetriever:
        """Read-only access to the underlying TiAssetRetriever, for a
        caller (Phase 23's VisualRenderer standalone TI_STATE mode) that
        needs canonical-asset identity/metadata without loading, resizing,
        or compositing any image bytes -- the read-only counterpart to
        composite()."""
        return self._retriever

    def composite(self, request: TiCompositeRequest) -> TiCompositeResult:
        ti_state = self._resolve_ti_state(request)
        asset = self._retriever.get_asset(ti_state)
        asset_path = self._retriever.resolve_path(asset)

        background = self._load_background(request.background_path)
        ti_image = self._load_ti_asset_image(asset_path)

        scaled_size = compute_scaled_size(
            ti_image.size, background.height, request.placement.scale.relative_height
        )
        ti_resized = ti_image.resize(scaled_size, Image.LANCZOS)

        requested_x, requested_y = compute_anchor_position(
            background.size,
            scaled_size,
            request.placement.anchor,
            request.placement.margin,
            request.placement.offset_x,
            request.placement.offset_y,
        )
        (final_x, final_y), clamped = clamp_position(
            background.size, scaled_size, (requested_x, requested_y)
        )

        composed = background.copy()
        composed.paste(ti_resized, (final_x, final_y), ti_resized)
        self._write_output(composed, request.output_path)

        return TiCompositeResult(
            output_path=request.output_path,
            resolved_ti_state=ti_state,
            source_visual_ti_state=request.visual_ti_state,
            background_width=background.width,
            background_height=background.height,
            ti_rendered_width=scaled_size[0],
            ti_rendered_height=scaled_size[1],
            requested_x=requested_x,
            requested_y=requested_y,
            placement_x=final_x,
            placement_y=final_y,
            clamped=clamped,
        )

    @staticmethod
    def _resolve_ti_state(request: TiCompositeRequest) -> TiState:
        """No fuzzy state inference: exactly one of the two fields on
        request already resolves to exactly one TiState. visual_ti_state
        goes through the existing deterministic to_ti_state() mapping
        (Phase 21.1); ti_state is used as-is."""
        if request.ti_state is not None:
            return request.ti_state
        assert request.visual_ti_state is not None  # enforced by TiCompositeRequest validation
        return to_ti_state(request.visual_ti_state)

    @staticmethod
    def _load_background(path: Path) -> Image.Image:
        if not path.is_file():
            raise TiCompositeBackgroundError(f"Background image not found: {path}")

        suffix = path.suffix.lower()
        if suffix not in _SUPPORTED_BACKGROUND_SUFFIXES:
            raise TiCompositeBackgroundError(
                f"Unsupported background format {suffix!r}; expected one of "
                f"{sorted(_SUPPORTED_BACKGROUND_SUFFIXES)}"
            )

        try:
            image = Image.open(path)
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise TiCompositeBackgroundError(f"Failed to read background image at {path}: {exc}") from exc

        # The background is always treated as the fully opaque base canvas --
        # any alpha channel a source PNG happens to carry is intentionally
        # dropped here (Phase 22 composites Tí onto a scene, it does not
        # itself produce a further-composable transparent layer).
        return image.convert("RGB")

    @staticmethod
    def _load_ti_asset_image(path: Path) -> Image.Image:
        try:
            image = Image.open(path)
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise TiCompositeAssetError(f"Failed to read canonical Tí asset image at {path}: {exc}") from exc
        return image.convert("RGBA")

    @staticmethod
    def _write_output(image: Image.Image, output_path: Path) -> None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
        except OSError as exc:
            raise TiCompositeWriteError(f"Failed to write composited output to {output_path}: {exc}") from exc


def compute_scaled_size(
    ti_size: tuple[int, int], background_height: int, relative_height: float
) -> tuple[int, int]:
    """Resize target for the Tí asset, aspect ratio preserved: rendered
    height = round(background_height * relative_height); rendered width
    follows from ti_size's own aspect ratio. Both dimensions are floored
    at 1px so a pathologically small relative_height can never produce a
    zero-size (i.e. invisible/degenerate) image."""
    ti_width, ti_height = ti_size
    target_height = max(1, round(background_height * relative_height))
    target_width = max(1, round(ti_width * target_height / ti_height))
    return target_width, target_height


def compute_anchor_position(
    background_size: tuple[int, int],
    ti_size: tuple[int, int],
    anchor: TiAnchor,
    margin: int,
    offset_x: int,
    offset_y: int,
) -> tuple[int, int]:
    """The requested (pre-clamp) top-left pixel position for ti_size on
    background_size, per TiPlacement's documented anchor/margin/offset
    semantics. May return a position that puts Tí partially or fully
    out-of-frame -- clamp_position() resolves that."""
    bg_width, bg_height = background_size
    ti_width, ti_height = ti_size

    if anchor is TiAnchor.BOTTOM_LEFT:
        x, y = margin, bg_height - ti_height - margin
    elif anchor is TiAnchor.BOTTOM_CENTER:
        x, y = (bg_width - ti_width) // 2, bg_height - ti_height - margin
    elif anchor is TiAnchor.BOTTOM_RIGHT:
        x, y = bg_width - ti_width - margin, bg_height - ti_height - margin
    elif anchor is TiAnchor.CENTER_LEFT:
        x, y = margin, (bg_height - ti_height) // 2
    elif anchor is TiAnchor.CENTER:
        x, y = (bg_width - ti_width) // 2, (bg_height - ti_height) // 2
    elif anchor is TiAnchor.CENTER_RIGHT:
        x, y = bg_width - ti_width - margin, (bg_height - ti_height) // 2
    else:  # pragma: no cover -- unreachable for a real TiAnchor member
        raise AssertionError(f"unhandled TiAnchor: {anchor!r}")

    return x + offset_x, y + offset_y


def clamp_position(
    background_size: tuple[int, int], ti_size: tuple[int, int], position: tuple[int, int]
) -> tuple[tuple[int, int], bool]:
    """Apply this compositor's clamp-first out-of-bounds policy (see this
    module's docstring). Raises TiCompositeBoundsError only when ti_size
    cannot possibly fit inside background_size regardless of position.
    Returns ((x, y), clamped) where clamped is True iff position had to
    move to land in-bounds."""
    bg_width, bg_height = background_size
    ti_width, ti_height = ti_size
    x, y = position

    if ti_width > bg_width or ti_height > bg_height:
        raise TiCompositeBoundsError(
            f"rendered Tí size {ti_width}x{ti_height} exceeds background size "
            f"{bg_width}x{bg_height}; reduce placement.scale.relative_height"
        )

    clamped_x = min(max(x, 0), bg_width - ti_width)
    clamped_y = min(max(y, 0), bg_height - ti_height)
    clamped = (clamped_x, clamped_y) != (x, y)
    return (clamped_x, clamped_y), clamped
