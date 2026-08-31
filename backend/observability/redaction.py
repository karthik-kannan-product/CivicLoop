from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from opentelemetry.util.types import AttributeValue

SafeAttributeValue: TypeAlias = (
    str
    | bool
    | int
    | float
    | Sequence[str]
    | Sequence[bool]
    | Sequence[int]
    | Sequence[float]
)

ALLOWED_SPAN_ATTRIBUTES = frozenset(
    {
        "openinference.span.kind",
        "civicloop.run_id",
        "civicloop.step_id",
        "civicloop.workflow_id",
        "civicloop.revision_id",
        "civicloop.revision_version",
        "civicloop.package_hash",
        "civicloop.schema_version",
        "civicloop.policy_version",
        "civicloop.capability_profile",
        "civicloop.provider",
        "civicloop.model",
        "civicloop.input_tokens",
        "civicloop.output_tokens",
        "civicloop.cost_microusd",
        "civicloop.fallback_category",
        "civicloop.approval_state",
        "civicloop.connector_category",
        "civicloop.evaluation_labels",
        "civicloop.trace_id",
        "civicloop.duration_ms",
        "civicloop.retry_count",
        "civicloop.workflow_status",
        "civicloop.stage",
        "civicloop.outcome",
        "civicloop.failure_category",
        "civicloop.fixture_id",
        "civicloop.fixture_hash",
    }
)

_PROHIBITED_VALUE = re.compile(
    r"(?i)(bearer\s+|api[_ -]?key|password|recovery[_ -]?code|totp|session[_ -]?cookie|"
    r"authorization|private[_ -]?key|secret|sk-[a-z0-9_-]{6,})"
)
_TRUNCATION_MARKER = "...[truncated]"


def _bounded_string(value: str, max_length: int) -> str:
    if _PROHIBITED_VALUE.search(value):
        return "[REDACTED]"
    if len(value) <= max_length:
        return value
    retained = max(max_length - len(_TRUNCATION_MARKER), 0)
    return f"{value[:retained]}{_TRUNCATION_MARKER}"[-max_length:]


def _sanitize_value(value: AttributeValue, max_length: int) -> SafeAttributeValue | None:
    if isinstance(value, str):
        return _bounded_string(value, max_length)
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Sequence):
        bounded = list(value[:16])
        if all(isinstance(item, str) for item in bounded):
            return tuple(_bounded_string(item, max_length) for item in bounded)
        if all(isinstance(item, bool) for item in bounded):
            return tuple(bounded)
        if all(isinstance(item, int) and not isinstance(item, bool) for item in bounded):
            return tuple(bounded)
        if all(isinstance(item, float) for item in bounded):
            return tuple(bounded)
    return None


def sanitize_span_attributes(
    attributes: Mapping[str, AttributeValue] | None,
    *,
    max_length: int,
) -> dict[str, SafeAttributeValue]:
    sanitized: dict[str, SafeAttributeValue] = {}
    for key, value in (attributes or {}).items():
        if key not in ALLOWED_SPAN_ATTRIBUTES:
            continue
        safe_value = _sanitize_value(value, max_length)
        if safe_value is not None:
            sanitized[key] = safe_value
    return sanitized
