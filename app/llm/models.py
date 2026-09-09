"""Provider-independent LLM request/response contracts and structured-output metadata."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

from app.models.common import MotilyModel, non_blank

T = TypeVar("T", bound=BaseModel)


class LLMRequest(MotilyModel):
    system_prompt: str | None = None
    user_prompt: str
    model: str
    temperature: float | None = None
    max_output_tokens: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_invariants(self) -> "LLMRequest":
        non_blank(self.user_prompt, "user_prompt")
        non_blank(self.model, "model")
        if self.temperature is not None and self.temperature < 0:
            raise ValueError("temperature must be >= 0")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be > 0")
        return self


class TokenUsage(MotilyModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TokenUsage":
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError(
                "total_tokens must equal input_tokens + output_tokens when all three are present"
            )
        return self


class LLMResponse(MotilyModel):
    text: str
    provider: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    provider_request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_required_text(self) -> "LLMResponse":
        non_blank(self.provider, "provider")
        non_blank(self.model, "model")
        return self


class ValidationFailure(MotilyModel):
    """One failed structured-output attempt. Externally observable data only --
    no private chain-of-thought or hidden reasoning fields."""

    attempt: int
    error_type: str
    message: str
    raw_text: str | None = None


class StructuredGenerationResult(MotilyModel, Generic[T]):
    """A validated domain object plus the generation metadata needed for traceability."""

    value: T
    attempts: int
    response: LLMResponse
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
