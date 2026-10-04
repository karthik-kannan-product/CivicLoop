"""Bounded, run-scoped authority sent across the Hermes transport boundary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

_FIELDS = frozenset(
    {
        "run_id",
        "workflow_id",
        "revision_id",
        "revision_digest",
        "actor_id",
        "capability",
        "model_alias",
        "expires_at",
        "max_inferences",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_microusd",
    }
)
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_ACTOR = re.compile(r"[A-Za-z0-9_-]{1,50}")
_CAPABILITY = re.compile(r"cap_[A-Za-z0-9_-]{43,125}")
_MODEL_ALIAS = re.compile(r"[a-z][a-z0-9_-]{2,63}")
_DATE_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def _bounded_integer(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} is invalid")
    return value


def _matched(value: object, pattern: re.Pattern[str], *, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def scope_digest(token: str) -> str:
    """Identify a capability without retaining or disclosing its bearer value."""
    if not isinstance(token, str):
        raise ValueError("scope token is invalid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    run_id: str
    workflow_id: UUID
    revision_id: int
    revision_digest: str
    actor_id: str
    capability: str = field(repr=False)
    model_alias: str
    expires_at: datetime
    max_inferences: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_microusd: int

    @classmethod
    def from_dict(cls, value: object, *, now: datetime | None = None) -> ScopeBinding:
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise ValueError("transport scope fields are invalid")
        current = now if now is not None else datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("current time must be timezone-aware")
        workflow_value = value["workflow_id"]
        if not isinstance(workflow_value, str) or len(workflow_value) != 36:
            raise ValueError("workflow ID is invalid")
        try:
            workflow_id = UUID(workflow_value)
        except ValueError as error:
            raise ValueError("workflow ID is invalid") from error
        if str(workflow_id) != workflow_value:
            raise ValueError("workflow ID is invalid")
        expiry_value = value["expires_at"]
        if (
            not isinstance(expiry_value, str)
            or len(expiry_value) > 40
            or _DATE_TIME.fullmatch(expiry_value) is None
        ):
            raise ValueError("scope expiry is invalid")
        try:
            expires_at = datetime.fromisoformat(expiry_value)
        except ValueError as error:
            raise ValueError("scope expiry is invalid") from error
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("scope expiry must be timezone-aware")
        if not current < expires_at <= current + timedelta(minutes=10):
            raise ValueError("scope lifetime is invalid")
        return cls(
            run_id=_matched(value["run_id"], _RUN_ID, label="run ID"),
            workflow_id=workflow_id,
            revision_id=_bounded_integer(
                value["revision_id"], label="revision ID", maximum=2**63 - 1
            ),
            revision_digest=_matched(value["revision_digest"], _DIGEST, label="revision digest"),
            actor_id=_matched(value["actor_id"], _ACTOR, label="actor ID"),
            capability=_matched(value["capability"], _CAPABILITY, label="capability"),
            model_alias=_matched(value["model_alias"], _MODEL_ALIAS, label="model alias"),
            expires_at=expires_at,
            max_inferences=_bounded_integer(
                value["max_inferences"], label="inference budget", maximum=1_000
            ),
            max_input_tokens=_bounded_integer(
                value["max_input_tokens"], label="input token budget", maximum=1_000_000
            ),
            max_output_tokens=_bounded_integer(
                value["max_output_tokens"], label="output token budget", maximum=100_000
            ),
            max_cost_microusd=_bounded_integer(
                value["max_cost_microusd"], label="cost budget", maximum=1_000_000_000
            ),
        )

    def safe_metadata(self) -> dict[str, str | int]:
        """Return only the identifying and bounded fields safe for logs."""
        return {
            "scope_digest": scope_digest(self.capability),
            "run_id": self.run_id,
            "workflow_id": str(self.workflow_id),
            "revision_id": self.revision_id,
            "revision_digest": self.revision_digest,
            "actor_id": self.actor_id,
            "model_alias": self.model_alias,
            "expires_at": self.expires_at.isoformat(),
            "max_inferences": self.max_inferences,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_microusd": self.max_cost_microusd,
        }
