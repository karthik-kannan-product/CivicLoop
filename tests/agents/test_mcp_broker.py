import copy
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from agents.capabilities import AuthorizationDenied, issue_workflow_capability
from agents.models import WorkflowCapability
from django.utils import timezone
from launchloop.models import AuditEvent

from tests.agents.test_runs import create_workflow

pytestmark = pytest.mark.django_db
IDENTITY = "test-mcp-service-identity-0000000000"


@pytest.fixture
def broker(settings, tmp_path):
    identity_file = tmp_path / "mcp-identity"
    identity_file.write_text(IDENTITY)
    settings.CIVICLOOP_MCP_TOKEN_FILE = str(identity_file)
    workflow, revision, actor, _ = create_workflow()
    token = issue_workflow_capability(
        workflow_id=workflow.id,
        revision_id=revision.id,
        actor_id=actor.pk,
        tools=frozenset(
            {
                "get_event_revision",
                "get_policy_context",
                "request_clarification",
                "propose_campaign_drafts",
                "validate_proposal",
                "request_eventbrite_draft",
                "request_iterable_drafts",
                "get_operation_status",
            }
        ),
        lifetime_seconds=60,
    )
    arguments = {
        "workflow_id": str(workflow.id),
        "revision_id": revision.id,
        "actor_id": actor.pk,
        "correlation_id": str(uuid4()),
        "request_id": str(uuid4()),
        "idempotency_key": "request-key-00000001",
    }
    return workflow, token, arguments


def call(broker, tool="get_event_revision", **updates):
    from agents.mcp import dispatch_mcp_tool

    _, token, arguments = broker
    return dispatch_mcp_tool(
        tool_name=tool,
        arguments={**arguments, **updates},
        service_identity=IDENTITY,
        capability=token,
    )


def test_bound_call_retries_are_idempotent_and_audited(broker):
    first = call(broker)
    assert first["revision_id"] == broker[0].revision_id
    assert first["correlation_id"] == broker[2]["correlation_id"]
    assert call(broker) == first
    assert AuditEvent.objects.filter(action="mcp.tool_succeeded").count() == 1
    assert AuditEvent.objects.filter(action="mcp.tool_replayed").count() == 1
    audit = json.dumps(list(AuditEvent.objects.values_list("details", flat=True)))
    assert broker[1] not in audit and IDENTITY not in audit and "synthetic" not in audit


@pytest.mark.parametrize(
    "change",
    ["audience", "expiry", "revocation", "actor", "tool", "workflow", "revision", "disabled_actor"],
)
def test_authorization_bindings_fail_generically(broker, change):
    record = WorkflowCapability.objects.get()
    if change == "audience":
        record.audience = "other"
    elif change == "expiry":
        record.expires_at = timezone.now() - timedelta(seconds=1)
    elif change == "revocation":
        record.revoked_at = timezone.now()
    elif change == "tool":
        record.tools = ["get_policy_context"]
    elif change == "disabled_actor":
        record.actor.user.is_active = False
        record.actor.user.save()
    else:
        broker[2][change + "_id"] = {
            "actor": "other",
            "workflow": str(uuid4()),
            "revision": 999999,
        }[change]
    record.save()
    with pytest.raises(AuthorizationDenied, match="^Authorization failed\\.$"):
        call(broker)
    assert AuditEvent.objects.filter(action="mcp.tool_denied").exists()


def test_changed_revision_correlation_or_replay_cannot_reuse_authority(broker):
    call(broker)
    for updates in ({"correlation_id": str(uuid4())}, {"idempotency_key": "different-key-0001"}):
        with pytest.raises(AuthorizationDenied):
            call(broker, **updates)
    from launchloop.models import EventRevision

    newer = EventRevision.objects.create(
        event=broker[0].event, version=2, author=broker[0].revision.author, snapshot={}
    )
    broker[0].revision = newer
    broker[0].save()
    with pytest.raises(AuthorizationDenied):
        call(broker)


@pytest.mark.parametrize("identity,token", [("bad", None), (IDENTITY, "cap_tampered")])
def test_identity_and_token_fail_generically(broker, identity, token):
    from agents.mcp import dispatch_mcp_tool

    with pytest.raises(AuthorizationDenied, match="^Authorization failed\\.$"):
        dispatch_mcp_tool(
            tool_name="get_event_revision",
            arguments=broker[2],
            service_identity=identity,
            capability=token or broker[1],
        )


@pytest.mark.parametrize(
    "value",
    [
        {"unexpected": "content"},
        {"actor_id": True},
        {"request_id": "invalid"},
        {"payload": "x" * 70000},
    ],
)
def test_exact_schema_and_payload_limits(broker, value):
    from agents.mcp import InvalidToolArguments

    with pytest.raises(InvalidToolArguments):
        call(broker, **value)


def test_provider_requests_only_persist_pending_operations(broker, monkeypatch):
    import socket

    from agents.models import DraftOperation

    def forbidden(*args, **kwargs):
        pytest.fail("Provider or adapter network access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    proposal = {
        "event_copy": "Synthetic draft",
        "invitation": {"subject": "Invite", "body": "Copy"},
        "reminder": {"subject": "Reminder", "body": "Copy"},
        "social": {"body": "Copy"},
    }
    result = call(broker, "propose_campaign_drafts", proposal=proposal)
    for index, tool in enumerate(["request_eventbrite_draft", "request_iterable_drafts"]):
        args = {
            "proposal_id": result["proposal_id"],
            "request_id": str(uuid4()),
            "idempotency_key": f"provider-request-{index:04}",
        }
        requested = call(broker, tool, **args)
        assert call(broker, tool, **args) == requested
        assert all(
            op["status"] == "pending" and op["approval"] is None and op["receipt"] is None
            for op in requested["operations"]
        )
    assert DraftOperation.objects.count() == 3
    assert set(DraftOperation.objects.values_list("status", flat=True)) == {"pending"}
    assert all(
        len(value) == 64 for value in DraftOperation.objects.values_list("action_digest", flat=True)
    )
    from pathlib import Path

    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/integrations/draft-operation.schema.json"
        ).read_text()
    )
    for operation in requested["operations"]:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(operation)
    changed = copy.deepcopy(proposal)
    changed["invitation"]["extra"] = "not permitted"
    from agents.mcp import InvalidToolArguments

    with pytest.raises(InvalidToolArguments):
        call(broker, "propose_campaign_drafts", proposal=changed, request_id=str(uuid4()))


def test_http_mcp_is_private_authenticated_and_jsonrpc(broker, settings, client):
    path = "/internal/v1/mcp"
    assert client.post(path).status_code == 404
    settings.ROOT_URLCONF = "agents.mcp_urls"
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert client.post(path, data=init, content_type="application/json").status_code == 401
    headers = {"HTTP_AUTHORIZATION": f"Bearer {IDENTITY}"}
    initialized = client.post(path, data=init, content_type="application/json", **headers)
    assert initialized.status_code == 200
    assert initialized.json()["result"]["capabilities"] == {"tools": {}}
    listed = client.post(
        path,
        data={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        content_type="application/json",
        **headers,
    )
    assert len(listed.json()["result"]["tools"]) == 8
    invocation = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_event_revision", "arguments": broker[2]},
    }
    assert (
        client.post(path, data=invocation, content_type="application/json", **headers).status_code
        == 401
    )
    response = client.post(
        path,
        data=invocation,
        content_type="application/json",
        HTTP_X_CIVICLOOP_CAPABILITY=broker[1],
        **headers,
    )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["revision_id"] == broker[0].revision_id
    assert response["Cache-Control"] == "no-store"
    assert client.get("/api/v1/agent-runs/" + str(uuid4())).status_code == 404


def test_untrusted_nesting_and_snapshot_size_are_bounded(broker):
    from agents.mcp import InvalidToolArguments
    from agents.tool_schemas import bounded_json

    nested = {}
    for _ in range(10):
        nested = {"child": nested}
    with pytest.raises(InvalidToolArguments):
        bounded_json(nested)
    broker[0].revision.snapshot = {"title": "x" * 70000}
    broker[0].revision.save()
    with pytest.raises(InvalidToolArguments):
        call(broker)


def test_tool_spans_never_capture_arguments_results_or_exceptions(broker, monkeypatch):
    from agents import mcp
    from observability.runtime import TelemetryRuntime
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TelemetryRuntime(provider, provider.get_tracer("test"))
    monkeypatch.setattr(mcp, "get_runtime", lambda: runtime)
    call(
        broker,
        "request_clarification",
        questions=[{"field": "venue_name", "question": "PRIVATE-CONTENT"}],
    )
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert set(spans[0].attributes) == {
        "openinference.span.kind",
        "civicloop.stage",
        "civicloop.outcome",
    }
    assert not spans[0].events
    assert "PRIVATE-CONTENT" not in str(spans[0].attributes)
    assert broker[1] not in str(spans[0].attributes)


def test_foreign_proposals_and_operations_are_denied(broker):
    from agents.models import DraftOperation, MCPSubmission

    other, revision, actor, _ = create_workflow()
    other_token = issue_workflow_capability(
        workflow_id=other.id,
        revision_id=revision.id,
        actor_id=actor.pk,
        tools=frozenset({"get_event_revision"}),
        lifetime_seconds=60,
    )
    assert other_token != broker[1]
    capability = WorkflowCapability.objects.get(workflow=other)
    proposal = MCPSubmission.objects.create(
        capability=capability, kind="proposal", content={}, digest="a" * 64
    )
    operation = DraftOperation.objects.create(
        workflow=other,
        revision=revision,
        actor=actor,
        proposal=proposal,
        provider="eventbrite",
        operation_kind="create_eventbrite_draft",
        action_digest="b" * 64,
        idempotency_key="c" * 64,
    )
    for tool in ("validate_proposal", "request_eventbrite_draft", "request_iterable_drafts"):
        with pytest.raises(AuthorizationDenied):
            call(broker, tool, proposal_id=str(proposal.id))
    with pytest.raises(AuthorizationDenied):
        call(broker, "get_operation_status", operation_id=str(operation.id))


def test_audit_failure_rolls_back_submission_and_replay_receipt(broker, monkeypatch):
    from agents.models import MCPInvocation, MCPSubmission
    from django.db import DatabaseError

    original = AuditEvent.objects.create

    def failed_audit(**kwargs):
        if kwargs["action"] == "mcp.tool_succeeded":
            raise DatabaseError("synthetic audit persistence failure")
        return original(**kwargs)

    monkeypatch.setattr(AuditEvent.objects, "create", failed_audit)
    with pytest.raises(DatabaseError):
        call(
            broker,
            "request_clarification",
            questions=[{"field": "venue_name", "question": "Where?"}],
        )
    assert not MCPSubmission.objects.exists()
    assert not MCPInvocation.objects.exists()
    assert WorkflowCapability.objects.get().correlation_id is None


def test_safe_reads_and_validation_do_not_grant_execution(broker):
    from agents.tool_schemas import TOOL_SCHEMAS
    from jsonschema import Draft202012Validator

    for schema in TOOL_SCHEMAS.values():
        Draft202012Validator.check_schema(schema)
    assert call(broker, "get_policy_context")["provider_execution_allowed"] is False
    broker[2].update(request_id=str(uuid4()), idempotency_key="new-request-000001")
    proposal = {
        "event_copy": "Draft",
        "invitation": {"subject": "Invite", "body": "Copy"},
        "reminder": {"subject": "Reminder", "body": "Copy"},
        "social": {"body": "Copy"},
    }
    reference = call(broker, "propose_campaign_drafts", proposal=proposal)
    broker[2].update(request_id=str(uuid4()), idempotency_key="new-request-000002")
    checked = call(broker, "validate_proposal", proposal_id=reference["proposal_id"])
    assert checked["schema_valid"] is True and checked["execution_authorized"] is False
    broker[2].update(request_id=str(uuid4()), idempotency_key="new-request-000003")
    requested = call(broker, "request_eventbrite_draft", proposal_id=reference["proposal_id"])
    broker[2].update(request_id=str(uuid4()), idempotency_key="new-request-000004")
    status = call(
        broker, "get_operation_status", operation_id=requested["operations"][0]["operation_id"]
    )
    assert status["status"] == "pending" and status["approval"] is None


def test_in_place_revision_mutation_invalidates_capability(broker):
    broker[0].revision.snapshot = {"title": "Changed authoritative facts"}
    broker[0].revision.save()
    with pytest.raises(AuthorizationDenied):
        call(broker)


def _propose(broker):
    return call(
        broker,
        "propose_campaign_drafts",
        proposal={
            "event_copy": "Draft",
            "invitation": {"subject": "Invite", "body": "Copy"},
            "reminder": {"subject": "Reminder", "body": "Copy"},
            "social": {"body": "Copy"},
        },
    )


def _reissue(broker):
    workflow, _, arguments = broker
    original = WorkflowCapability.objects.filter(workflow=workflow).first()
    token = issue_workflow_capability(
        workflow_id=workflow.id,
        revision_id=workflow.revision_id,
        actor_id=arguments["actor_id"],
        tools=frozenset(original.tools),
        lifetime_seconds=60,
    )
    return (
        workflow,
        token,
        {
            **arguments,
            "request_id": str(uuid4()),
            "idempotency_key": "fresh-capability-request-001",
        },
    )


@pytest.mark.parametrize("tool", ["request_eventbrite_draft", "request_iterable_drafts"])
def test_fresh_capability_cannot_request_old_content_after_same_pk_mutation(broker, tool):
    from agents.models import DraftOperation

    proposal = _propose(broker)
    broker[0].revision.snapshot = {"title": "Changed facts under the same revision PK"}
    broker[0].revision.save()
    fresh = _reissue(broker)
    with pytest.raises(AuthorizationDenied):
        call(fresh, tool, proposal_id=proposal["proposal_id"])
    assert not DraftOperation.objects.exists()


def test_status_references_are_bound_to_revision_content(broker):
    proposal = _propose(broker)
    broker[2].update(request_id=str(uuid4()), idempotency_key="request-operation-001")
    requested = call(broker, "request_eventbrite_draft", proposal_id=proposal["proposal_id"])
    broker[0].revision.snapshot = {"title": "Changed facts under the same revision PK"}
    broker[0].revision.save()
    fresh = _reissue(broker)
    with pytest.raises(AuthorizationDenied):
        call(fresh, "get_operation_status", operation_id=requested["operations"][0]["operation_id"])


def test_browser_origin_is_rejected_even_with_both_valid_credentials(broker, settings, client):
    from agents.models import MCPInvocation

    settings.ROOT_URLCONF = "agents.mcp_urls"
    response = client.post(
        "/internal/v1/mcp",
        content_type="application/json",
        data={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_event_revision", "arguments": broker[2]},
        },
        HTTP_AUTHORIZATION=f"Bearer {IDENTITY}",
        HTTP_X_CIVICLOOP_CAPABILITY=broker[1],
        HTTP_ORIGIN="https://browser.example.test",
    )
    assert response.status_code == 401
    assert response.json() == {"error": "Authorization failed."}
    assert not MCPInvocation.objects.exists()


def test_database_rejects_approval_receipts_and_nonpending_operation_states(broker):
    from agents.models import DraftOperation
    from django.db import IntegrityError, transaction
    from launchloop.models import ApprovalRequest

    proposal = _propose(broker)
    broker[2].update(request_id=str(uuid4()), idempotency_key="request-operation-001")
    requested = call(broker, "request_eventbrite_draft", proposal_id=proposal["proposal_id"])
    operation_id = requested["operations"][0]["operation_id"]
    approval = ApprovalRequest.objects.create(
        workflow=broker[0], submitter=broker[0].revision.author, package_hash=broker[0].package_hash
    )
    for mutation in (
        {"status": "approved"},
        {"receipt": {"receipt_type": "eventbrite_draft"}},
        {"approval_id": approval.id},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            DraftOperation.objects.filter(pk=operation_id).update(**mutation)
    operation = DraftOperation.objects.get(pk=operation_id)
    assert (
        operation.status == "pending"
        and operation.approval_id is None
        and operation.receipt is None
    )


@pytest.mark.parametrize(
    "tool,count", [("request_eventbrite_draft", 1), ("request_iterable_drafts", 2)]
)
def test_pending_operations_deduplicate_across_capabilities_for_unchanged_content(
    broker, tool, count
):
    from agents.models import DraftOperation

    proposal = _propose(broker)
    broker[2].update(request_id=str(uuid4()), idempotency_key="request-operation-001")
    original = call(broker, tool, proposal_id=proposal["proposal_id"])
    fresh = _reissue(broker)
    assert fresh[1] != broker[1]
    repeated = call(fresh, tool, proposal_id=proposal["proposal_id"])
    assert repeated["operations"] == original["operations"]
    assert DraftOperation.objects.count() == count
