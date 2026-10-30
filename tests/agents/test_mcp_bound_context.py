"""The model chooses tool inputs; the signed scope supplies authority."""
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from agents.models import DraftOperation, WorkflowCapability
from django.utils import timezone

from tests.agents import test_mcp_broker as broker_tests

IDENTITY = broker_tests.IDENTITY
broker = broker_tests.broker

pytestmark = pytest.mark.django_db
BOUND_FIELDS = {"workflow_id", "revision_id", "actor_id", "correlation_id"}


def bound_scope(broker):
    record = WorkflowCapability.objects.get()
    record.correlation_id = broker[2]["correlation_id"]
    record.save(update_fields=["correlation_id"])
    return record


def post(client, settings, token, method, params=None):
    settings.ROOT_URLCONF = "agents.mcp_urls"
    return client.post(
        "/internal/v1/mcp",
        data={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {IDENTITY}",
        HTTP_X_CIVICLOOP_CAPABILITY=token,
    )


def test_bound_model_schema_does_not_ask_model_to_copy_authority(broker, settings, client):
    bound_scope(broker)
    response = post(client, settings, broker[1], "tools/list")
    assert response.status_code == 200
    for tool in response.json()["result"]["tools"]:
        schema = tool["inputSchema"]
        assert not BOUND_FIELDS & set(schema["properties"])
        assert not BOUND_FIELDS & set(schema["required"])
        assert not {"request_id", "idempotency_key"} & set(schema["properties"])
        assert schema["additionalProperties"] is False


def test_read_tool_derives_exact_context_from_existing_capability(broker, settings, client):
    bound_scope(broker)
    args = {k: v for k, v in broker[2].items() if k not in BOUND_FIELDS}
    response = post(client, settings, broker[1], "tools/call",
                    {"name": "get_event_revision", "arguments": args})
    assert response.status_code == 200
    result = response.json()["result"]["structuredContent"]
    assert result["revision_id"] == broker[0].revision_id
    assert result["correlation_id"] == broker[2]["correlation_id"]
    assert DraftOperation.objects.count() == 0


@pytest.mark.parametrize("field", sorted(BOUND_FIELDS))
def test_explicit_foreign_binding_still_fails_closed(broker, settings, client, field):
    bound_scope(broker)
    args = dict(broker[2])
    args[field] = 999999 if field == "revision_id" else (
        "foreign-actor" if field == "actor_id" else str(uuid4()))
    response = post(client, settings, broker[1], "tools/call",
                    {"name": "get_event_revision", "arguments": args})
    assert response.status_code == 401
    assert DraftOperation.objects.count() == 0


def test_revoked_scope_cannot_get_bound_schema_or_execute(broker, settings, client):
    record = bound_scope(broker)
    record.revoked_at = timezone.now()
    record.save(update_fields=["revoked_at"])
    assert post(client, settings, broker[1], "tools/list").status_code == 401
    assert post(client, settings, broker[1], "tools/call",
                {"name": "get_event_revision", "arguments": broker[2]}).status_code == 401


def test_schema_only_advertises_tools_in_signed_scope(broker, settings, client):
    record = bound_scope(broker)
    record.tools = ["get_event_revision"]
    record.save(update_fields=["tools"])
    response = post(client, settings, broker[1], "tools/list")
    assert [tool["name"] for tool in response.json()["result"]["tools"]] == [
        "get_event_revision"
    ]


@pytest.mark.parametrize("supplied", [BOUND_FIELDS, {"workflow_id"}, set()])
def test_matching_partial_or_full_context_replays_safely(broker, settings, client, supplied):
    bound_scope(broker)
    args = {k: v for k, v in broker[2].items() if k not in BOUND_FIELDS or k in supplied}
    params = {"name": "get_event_revision", "arguments": args}
    first = post(client, settings, broker[1], "tools/call", params)
    second = post(client, settings, broker[1], "tools/call", params)
    assert first.status_code == second.status_code == 200
    original, replayed = first.json(), second.json()
    assert original["id"] == replayed["id"] == 1
    assert original["result"]["structuredContent"] == replayed["result"]["structuredContent"]
    for result in (original["result"], replayed["result"]):
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_equal_boolean_is_not_an_authorized_revision_integer(broker, settings, client):
    bound_scope(broker)
    args = dict(broker[2], revision_id=True)
    assert post(client, settings, broker[1], "tools/call",
                {"name": "get_event_revision", "arguments": args}).status_code == 401


@pytest.mark.parametrize("change", ["revocation", "expiry", "correlation"])
@pytest.mark.parametrize("managed", [False, True])
def test_broker_rechecks_authority_after_context_resolution(
    broker, settings, client, change, managed,
):
    from agents.mcp import bound_tool_context

    record = bound_scope(broker)
    args = {k: v for k, v in broker[2].items() if k not in BOUND_FIELDS}
    if managed:
        args = {k: v for k, v in args.items() if k not in {"request_id", "idempotency_key"}}

    def resolve_then_change(token):
        context = bound_tool_context(token)
        if change == "revocation":
            record.revoked_at = timezone.now()
        elif change == "expiry":
            record.expires_at = timezone.now() - timedelta(seconds=1)
        else:
            record.correlation_id = uuid4()
        record.save()
        return context

    with patch("agents.mcp.bound_tool_context", side_effect=resolve_then_change):
        response = post(client, settings, broker[1], "tools/call",
                        {"name": "get_event_revision", "arguments": args})
    assert response.status_code == 401
    assert DraftOperation.objects.count() == 0
