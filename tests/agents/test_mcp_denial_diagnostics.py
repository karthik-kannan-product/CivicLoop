"""Denied calls expose closed diagnostic labels, never caller data."""
import json
from uuid import uuid4

import pytest
from agents.capabilities import AuthorizationDenied
from launchloop.models import AuditEvent

from tests.agents import test_mcp_broker as broker_tests

broker = broker_tests.broker
call = broker_tests.call

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("change", ["tool", "request"])
def test_replay_conflict_is_distinguished_without_retaining_arguments(broker, change):
    call(broker)
    updates = {"tool": "get_policy_context"} if change == "tool" else {
        "request_id": str(uuid4())
    }
    with pytest.raises(AuthorizationDenied, match="^Authorization failed[.]$"):
        call(broker, **updates)
    details = AuditEvent.objects.get(action="mcp.tool_denied").details
    assert details == {
        "reason": "authorization_failed", "stage": "invocation_replay_conflict",
        "tool": "get_policy_context" if change == "tool" else "get_event_revision",
    }
    encoded = json.dumps(details)
    assert broker[1] not in encoded and broker[2]["request_id"] not in encoded
    assert broker[2]["idempotency_key"] not in encoded


def test_missing_proposal_has_its_own_closed_diagnostic(broker):
    with pytest.raises(AuthorizationDenied, match="^Authorization failed[.]$"):
        call(broker, tool="validate_proposal", proposal_id=str(uuid4()))
    details = AuditEvent.objects.get(action="mcp.tool_denied").details
    assert details["stage"] == "proposal_unavailable"
    assert details["tool"] == "validate_proposal"


def test_unrecognized_diagnostic_text_is_never_retained():
    from agents.mcp import _BrokerDenial

    error = _BrokerDenial("private credential or caller text")
    assert str(error) == "Authorization failed."
    assert error.stage == "authorization_failed"
