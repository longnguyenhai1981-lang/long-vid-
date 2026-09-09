"""LLM-layer domain errors.

Validation failure (the provider answered, but content didn't satisfy the
contract) and provider failure (the invocation itself failed) are kept
strictly separate. The structured-output layer retries the former and never
the latter -- see docs/TECHNICAL_SPEC_v0.1.md.
"""

from __future__ import annotations

from app.llm.models import ValidationFailure


class LLMError(Exception):
    """Base class for all LLM-layer errors."""


class LLMProviderError(LLMError):
    """The provider invocation itself failed (network/API/provider-side error)."""


class StructuredOutputError(LLMError):
    """One structured-output attempt failed to parse as JSON or validate against the schema."""

    def __init__(self, error_type: str, message: str, raw_text: str | None = None):
        self.error_type = error_type
        self.message = message
        self.raw_text = raw_text
        super().__init__(message)


class StructuredOutputExhaustedError(LLMError):
    """All structured-output attempts (initial call plus retries) were exhausted."""

    def __init__(self, attempts: int, validation_failures: list[ValidationFailure]):
        self.attempts = attempts
        self.validation_failures = validation_failures
        last = validation_failures[-1].message if validation_failures else "unknown error"
        super().__init__(f"Structured output failed after {attempts} attempt(s): {last}")
