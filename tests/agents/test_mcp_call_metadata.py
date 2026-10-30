"""The model supplies content; the server supplies scoped retry identifiers."""
from uuid import uuid4

import pytest
from agents.capabilities import issue_workflow_capability, token_digest
from agents.models import MCPInvocation, MCPSubmission, WorkflowCapability

from tests.agents import test_mcp_bound_context as bound_tests

broker = bound_tests.broker
post = bound_tests.post
bound_scope = bound_tests.bound_scope
pytestmark = pytest.mark.django_db


def test_read_tools_without_retry_fields_are_distinct_and_replay_safely(broker, settings, client):
    bound_scope(broker)
    responses = [post(client, settings, broker[1], "tools/call", {
        "name": tool, "arguments": {},
    }) for tool in ("get_event_revision", "get_policy_context", "get_event_revision")]
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert responses[0].json() == responses[2].json()
    assert MCPInvocation.objects.count() == 2


def test_content_changes_create_distinct_calls_but_key_order_does_not(broker, settings, client):
    bound_scope(broker)
    values = [
        {"field": "venue", "question": "Which room?"},
        {"question": "Which room?", "field": "venue"},
        {"field": "venue", "question": "Which address?"},
    ]
    results = [post(client, settings, broker[1], "tools/call", {
        "name": "request_clarification", "arguments": {"questions": [value]},
    }) for value in values]
    assert [r.status_code for r in results] == [200, 200, 200]
    assert results[0].json() == results[1].json()
    assert results[0].json() != results[2].json()
    assert MCPSubmission.objects.count() == MCPInvocation.objects.count() == 2


def test_retry_identifiers_are_isolated_between_capabilities(broker, settings, client):
    bound_scope(broker)
    second = issue_workflow_capability(
        workflow_id=broker[0].id, revision_id=broker[0].revision_id,
        actor_id=broker[2]["actor_id"], tools=frozenset({"get_event_revision"}),
        lifetime_seconds=60,
    )
    record = WorkflowCapability.objects.get(token_digest=token_digest(second))
    record.correlation_id = broker[2]["correlation_id"]
    record.save(update_fields=["correlation_id"])
    for token in (broker[1], second):
        assert post(client, settings, token, "tools/call", {
            "name": "get_event_revision", "arguments": {},
        }).status_code == 200
    assert MCPInvocation.objects.count() == 2


def test_explicit_metadata_keeps_original_replay_conflict_protection(broker, settings, client):
    bound_scope(broker)
    for tool, expected in (("get_event_revision", 200), ("get_policy_context", 401)):
        assert post(client, settings, broker[1], "tools/call", {
            "name": tool, "arguments": broker[2],
        }).status_code == expected
    assert MCPInvocation.objects.count() == 1


@pytest.mark.parametrize("arguments", [
    {"request_id": str(uuid4())}, {"idempotency_key": "partial-retry-key"},
    {"unknown": "not an accepted tool argument"},
])
def test_partial_metadata_or_unknown_fields_remain_invalid(broker, settings, client, arguments):
    bound_scope(broker)
    assert post(client, settings, broker[1], "tools/call", {
        "name": "get_event_revision", "arguments": arguments,
    }).status_code == 400
    assert MCPInvocation.objects.count() == 0


def test_generated_metadata_does_not_bypass_revocation(broker, settings, client):
    record = bound_scope(broker)
    from django.utils import timezone

    record.revoked_at = timezone.now()
    record.save(update_fields=["revoked_at"])
    assert post(client, settings, broker[1], "tools/call", {
        "name": "get_event_revision", "arguments": {},
    }).status_code == 401
    assert MCPInvocation.objects.count() == 0
