from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from deploy.hermes.transport_contracts import ScopeBinding, scope_digest

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
CAPABILITY = "cap_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"


def _scope(**updates: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "run_id": "run-123",
        "workflow_id": "843a756b-b9a4-4fb7-89ee-05be3f38fc6d",
        "revision_id": 1,
        "revision_digest": "a" * 64,
        "actor_id": "draft-operator",
        "capability": CAPABILITY,
        "model_alias": "event-draft-primary",
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
        "max_inferences": 3,
        "max_input_tokens": 16000,
        "max_output_tokens": 4000,
        "max_cost_microusd": 250000,
    }
    scope.update(updates)
    return scope


def test_transport_scope_accepts_bounded_binding_and_redacts_authority() -> None:
    binding = ScopeBinding.from_dict(_scope(), now=NOW)

    assert binding.workflow_id == UUID("843a756b-b9a4-4fb7-89ee-05be3f38fc6d")
    assert binding.revision_id == 1
    assert scope_digest(CAPABILITY) == (
        "cb55fbcfe614ff2176eebb86950bc8f69d2d063ed88255a473716585cadc26b1"
    )
    safe = binding.safe_metadata()
    assert safe["scope_digest"] == scope_digest(CAPABILITY)
    assert CAPABILITY not in json.dumps(safe)
    assert "capability" not in safe
    assert CAPABILITY not in repr(binding)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("run_id", "bad run"),
        ("run_id", "run-123\n"),
        ("workflow_id", "not-a-uuid"),
        ("revision_id", True),
        ("revision_id", 0),
        ("revision_digest", "A" * 64),
        ("revision_digest", "a" * 64 + "\n"),
        ("actor_id", "user@example.com"),
        ("actor_id", "draft-operator\n"),
        ("capability", "raw-secret"),
        ("capability", CAPABILITY + "\n"),
        ("model_alias", "Invalid Model"),
        ("model_alias", "event-draft-primary\n"),
        ("expires_at", (NOW + timedelta(minutes=10, seconds=1)).isoformat()),
        ("expires_at", (NOW - timedelta(seconds=1)).isoformat()),
        ("expires_at", "2026-09-26 12:05:00+00:00"),
        ("expires_at", "2026-09-26T12:05:00"),
        ("expires_at", "2026-09-26T13:05:00+00:60"),
        ("max_inferences", True),
        ("max_inferences", 0),
        ("max_inferences", 1001),
        ("max_input_tokens", 0),
        ("max_input_tokens", 1_000_001),
        ("max_output_tokens", False),
        ("max_output_tokens", 100_001),
        ("max_cost_microusd", 0),
        ("max_cost_microusd", 1_000_000_001),
    ],
)
def test_transport_scope_rejects_invalid_binding(field: str, invalid: object) -> None:
    with pytest.raises(ValueError):
        ScopeBinding.from_dict(_scope(**{field: invalid}), now=NOW)


def test_transport_scope_rejects_unknown_or_missing_fields() -> None:
    with pytest.raises(ValueError):
        ScopeBinding.from_dict(_scope(unexpected="value"), now=NOW)
    missing = _scope()
    del missing["actor_id"]
    with pytest.raises(ValueError):
        ScopeBinding.from_dict(missing, now=NOW)
