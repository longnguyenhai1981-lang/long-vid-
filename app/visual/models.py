"""Provider-independent visual-render request/response contracts.

Mirrors app/audio/models.py's TTSRequest/TTSResponse split exactly: a
generic request/response pair that stays the same across every concrete
provider, with vendor-specific detail confined to `metadata`.

VisualRenderRequest is deliberately NOT an image-generation prompt -- it is
a provider-neutral render brief copied verbatim from one VisualBeat (see
app/renderers/visual/renderer.py). A concrete provider adapter, added in a
future phase, is responsible for translating this brief into whatever
vendor-specific prompt/parameters its API needs.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from app.models.common import MotilyModel, VisualMediaType, VisualOutputFormat, VisualTiState, non_blank


class VisualRenderRequest(MotilyModel):
    render_job_id: str
    beat_id: str
    media_type: VisualMediaType
    concept: str
    primary_focus: str
    secondary_elements: list[str] = Field(default_factory=list)
    context_elements: list[str] = Field(default_factory=list)
    ti_state: VisualTiState | None = None
    motion_intent: str | None = None
    reuse_key: str | None = None
    output_format: VisualOutputFormat
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_invariants(self) -> "VisualRenderRequest":
        non_blank(self.render_job_id, "render_job_id")
        non_blank(self.beat_id, "beat_id")
        non_blank(self.concept, "concept")
        non_blank(self.primary_focus, "primary_focus")
        return self


class VisualRenderResponse(MotilyModel):
    """A provider's render result.

    asset_bytes holds the raw encoded image in memory only. VisualRenderResponse
    is never persisted (no artifact type, no ModuleRun field, no ORM column
    stores it) and never JSON-serialized -- the renderer reads asset_bytes
    once, writes it to a file via VisualFileStore, and discards the
    response. Only the resulting file path is ever recorded (see
    app/models/visual_render.py's RenderedVisualAsset.file_path).
    """

    asset_bytes: bytes
    provider: str
    model: str | None = None
    output_format: VisualOutputFormat
    width: int | None = None
    height: int | None = None
    provider_request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_invariants(self) -> "VisualRenderResponse":
        if not self.asset_bytes:
            raise ValueError("asset_bytes cannot be empty")
        non_blank(self.provider, "provider")
        if self.width is not None and self.width <= 0:
            raise ValueError("width must be > 0 when supplied")
        if self.height is not None and self.height <= 0:
            raise ValueError("height must be > 0 when supplied")
        return self
