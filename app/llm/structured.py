"""Provider-independent structured-output generation with bounded validation retries.

Engine -> StructuredGenerator (this module) -> LLMProvider -> concrete provider.
Provider-specific details never appear above the LLMProvider boundary.
"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.errors import StructuredOutputError, StructuredOutputExhaustedError
from app.llm.models import LLMRequest, StructuredGenerationResult, ValidationFailure
from app.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

_MAX_RAW_OUTPUT_CHARS = 2000


def generate_structured(
    provider: LLMProvider,
    request: LLMRequest,
    output_model: type[T],
    max_retries: int = 2,
) -> "StructuredGenerationResult[T]":
    """Call provider.generate until output_model validates, or attempts are exhausted.

    max_retries=2 means one initial attempt plus up to two retries: at most
    three total provider calls. Only JSON-parse/schema-validation failures
    trigger a retry -- if provider.generate itself raises (e.g.
    LLMProviderError), that propagates immediately and is never retried here.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")

    total_attempts = max_retries + 1
    failures: list[ValidationFailure] = []
    current_request = request

    for attempt in range(1, total_attempts + 1):
        response = provider.generate(current_request)

        try:
            value = _parse_and_validate(response.text, output_model)
        except StructuredOutputError as exc:
            failures.append(
                ValidationFailure(
                    attempt=attempt,
                    error_type=exc.error_type,
                    message=exc.message,
                    raw_text=exc.raw_text,
                )
            )
            if attempt >= total_attempts:
                raise StructuredOutputExhaustedError(
                    attempts=attempt, validation_failures=failures
                ) from exc
            current_request = _build_correction_request(request, output_model, exc)
            continue

        return StructuredGenerationResult(
            value=value,
            attempts=attempt,
            response=response,
            validation_failures=failures,
        )

    raise AssertionError("unreachable: generate_structured loop exited without a result")


def _parse_and_validate(text: str, output_model: type[T]) -> T:
    cleaned = _strip_outer_code_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            error_type="json_decode_error",
            message=f"Response was not valid JSON: {exc}",
            raw_text=text,
        ) from exc

    try:
        return output_model.model_validate(data)
    except ValidationError as exc:
        summary = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise StructuredOutputError(
            error_type="schema_validation_error",
            message=summary,
            raw_text=text,
        ) from exc


def _strip_outer_code_fence(text: str) -> str:
    """Defensively strip a single outer ``` or ```json fence around the WHOLE response.

    Only fires when the entire trimmed text is one fence block. This never
    scans for a JSON substring embedded in surrounding prose -- that case is
    left to fail validation rather than being silently repaired.
    """
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```") and len(stripped) > 6):
        return stripped
    inner = stripped[3:-3]
    lines = inner.split("\n", 1)
    if len(lines) == 2 and lines[0].strip().lower() in ("json", ""):
        inner = lines[1]
    return inner.strip()


def _build_correction_request(
    original_request: LLMRequest,
    output_model: type[BaseModel],
    failure: StructuredOutputError,
) -> LLMRequest:
    """Build a fresh correction request from the ORIGINAL task plus the latest failure only.

    This is a correction attempt, not an accumulating conversation: earlier
    failures are not replayed, no stack trace leaks in, and the model is not
    asked to explain itself -- only to return valid JSON matching the schema.
    """
    schema_json = json.dumps(output_model.model_json_schema())
    raw_output = failure.raw_text or ""
    if len(raw_output) > _MAX_RAW_OUTPUT_CHARS:
        raw_output = raw_output[:_MAX_RAW_OUTPUT_CHARS] + "... (truncated)"

    lines = [
        "Your previous response failed output validation and could not be used.",
        f"Validation problem: {failure.message}",
        "",
        "Return ONLY a single JSON object matching this schema exactly -- "
        "no prose, no markdown code fences, no explanation:",
        schema_json,
    ]
    if raw_output:
        lines += ["", "Your previous output was:", raw_output]
    lines += ["", "Original task:", original_request.user_prompt]

    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})
