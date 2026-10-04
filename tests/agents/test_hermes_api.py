import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from agents.models import AgentRun, DraftOperation, MCPSubmission, WorkflowCapability
from django.test import Client
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from tests.agents import test_hermes_tasks
from tests.agents.test_runs import create_run
from tests.identity.test_security_actions_api import create_authenticated_owner

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def api_configuration(settings):
    settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED = True
    settings.CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED = True
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def inputs(settings, monkeypatch):
    return test_hermes_tasks.inputs.__wrapped__(settings, monkeypatch)


@pytest.fixture
def owner(inputs):
    client, profile, metadata, _ = create_authenticated_owner()
    return client, profile, metadata


def validate(payload, filename):
    schema = json.loads((ROOT / "schemas" / "agents" / filename).read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def start(client, inputs, *, key=None, body=None, **kwargs):
    workflow, revision, _, _ = inputs
    return client.post(
        f"/api/v1/workflows/{workflow.id}/hermes-runs",
        data=json.dumps({"revision_id": revision.id} if body is None else body),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(key or uuid.uuid4()),
        **kwargs,
    )


def accepted(owner, inputs):
    response = start(owner[0], inputs)
    assert response.status_code == 202
    return AgentRun.objects.get(id=response.json()["run_id"])


def paths(run):
    base = f"/api/v1/agent-runs/{run.id}"
    return base, base + "/pending-operations", base + "/cancel"


def test_owner_start_is_closed_queued_schema_and_no_external_action(owner, inputs):
    response = start(owner[0], inputs)
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    validate(response.json(), "hermes-start.schema.json")
    run = AgentRun.objects.get(id=response.json()["run_id"])
    assert run.event_revision_id == inputs[1].id
    assert run.hermes_binding.actor.user_id == owner[1].user_id
    assert not DraftOperation.objects.exists()


@pytest.mark.parametrize(
    "identity,expected",
    [("anonymous", 401), ("operator", 403), ("approver", 403), ("recovery", 403)],
)
def test_start_requires_full_owner_session(owner, inputs, identity, expected):
    client = Client()
    if identity == "recovery":
        client = owner[0]
        owner[2].recovery_restricted = True
        owner[2].save(update_fields=["recovery_restricted"])
    elif identity in {"operator", "approver"}:
        client.force_login(inputs[2 if identity == "operator" else 3].user)
    response = start(client, inputs)
    assert response.status_code == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert not AgentRun.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"revision_id": True},
        {"revision_id": 0},
        {"revision_id": -1},
        {"revision_id": "1"},
        {"revision_id": 1, "extra": "private-content"},
        [],
        None,
    ],
)
def test_start_rejects_nonexact_revision_body(owner, inputs, body):
    workflow = inputs[0]
    response = owner[0].post(
        f"/api/v1/workflows/{workflow.id}/hermes-runs",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 400
    assert "private-content" not in response.content.decode()
    assert not AgentRun.objects.exists()


@pytest.mark.parametrize(
    "raw,content_type",
    [
        ("{", "application/json"),
        (" " * 1025, "application/json"),
        ('{"revision_id":1}', "text/plain"),
        ('{"revision_id":1,"revision_id":2}', "application/json"),
    ],
)
def test_start_rejects_bad_encoding_size_content_type_and_duplicate_keys(
    owner, inputs, raw, content_type
):
    response = owner[0].post(
        f"/api/v1/workflows/{inputs[0].id}/hermes-runs",
        data=raw,
        content_type=content_type,
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 400
    assert not AgentRun.objects.exists()


@pytest.mark.parametrize("key", [None, "", "not-a-uuid", "a" * 1025])
def test_start_requires_bounded_uuid_idempotency_key(owner, inputs, key):
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    response = owner[0].post(
        f"/api/v1/workflows/{inputs[0].id}/hermes-runs",
        data=json.dumps({"revision_id": inputs[1].id}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 400
    assert not AgentRun.objects.exists()


def test_same_key_replays_original_queued_receipt_after_run_state_changes(owner, inputs):
    key = uuid.uuid4()
    original = start(owner[0], inputs, key=key)
    assert original.status_code == 202
    AgentRun.objects.filter(pk=original.json()["run_id"]).update(
        status="cancelled", failure_category="cancelled", finished_at=timezone.now()
    )
    replay = start(owner[0], inputs, key=key)
    assert replay.status_code == 202
    assert replay.json() == original.json()
    assert AgentRun.objects.count() == 1


def test_same_key_different_revision_conflicts(owner, inputs):
    key = uuid.uuid4()
    assert start(owner[0], inputs, key=key).status_code == 202
    response = start(owner[0], inputs, key=key, body={"revision_id": inputs[1].id + 1})
    assert response.status_code == 409
    assert AgentRun.objects.count() == 1


@pytest.mark.parametrize("reason", ["stale", "not_ready", "global_busy"])
def test_admission_conflicts_are_409(owner, inputs, reason):
    body = None
    if reason == "stale":
        body = {"revision_id": inputs[1].id + 1}
    elif reason == "not_ready":
        inputs[0].status = "needs_input"
        inputs[0].save(update_fields=["status", "updated_at"])
    else:
        assert start(owner[0], inputs).status_code == 202
    response = start(owner[0], inputs, body=body)
    assert response.status_code == 409


@pytest.mark.parametrize(
    "flag", ["CIVICLOOP_HERMES_ENABLED", "CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED"]
)
def test_disabled_gate_returns_503(owner, inputs, settings, flag):
    setattr(settings, flag, False)
    assert start(owner[0], inputs).status_code == 503
    assert not AgentRun.objects.exists()


def test_missing_workflow_returns_404(owner, inputs):
    response = owner[0].post(
        f"/api/v1/workflows/{uuid.uuid4()}/hermes-runs",
        data=json.dumps({"revision_id": inputs[1].id}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 404


def test_start_and_cancel_enforce_csrf(owner, inputs):
    client = Client(enforce_csrf_checks=True)
    client.cookies = owner[0].cookies
    assert start(client, inputs).status_code == 403
    run = accepted(owner, inputs)
    assert client.post(paths(run)[2], data="{}", content_type="application/json").status_code == 403


def test_valid_csrf_token_allows_owner_start_and_cancel(owner, inputs):
    client = Client(enforce_csrf_checks=True)
    client.cookies = owner[0].cookies
    token = "a" * 32
    client.cookies["csrftoken"] = token
    response = start(client, inputs, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 202
    run = AgentRun.objects.get(id=response.json()["run_id"])
    cancellation = client.post(
        paths(run)[2], data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=token
    )
    assert cancellation.status_code == 202


@pytest.mark.parametrize("suffix", ["", "/pending-operations", "/cancel"])
def test_missing_run_returns_404_to_owner(owner, suffix):
    path = f"/api/v1/agent-runs/{uuid.uuid4()}{suffix}"
    response = (
        owner[0].post(path, data="{}", content_type="application/json")
        if suffix == "/cancel"
        else owner[0].get(path)
    )
    assert response.status_code == 404


def test_status_and_empty_preterminal_operations_are_bounded_and_private(owner, inputs):
    run = accepted(owner, inputs)
    status = owner[0].get(paths(run)[0])
    pending = owner[0].get(paths(run)[1])
    assert status.status_code == pending.status_code == 200
    validate(status.json(), "hermes-status.schema.json")
    validate(pending.json(), "pending-operation-page.schema.json")
    assert pending.json()["results"] == []
    assert status.json()["proposal_count"] == status.json()["pending_operation_count"] == 0
    assert "actor_id" not in status.json()
    assert status.headers["Cache-Control"] == pending.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("endpoint", [0, 1, 2])
@pytest.mark.parametrize(
    "identity,expected", [("anonymous", 401), ("approver", 403), ("recovery", 403)]
)
def test_all_hermes_run_routes_require_owner(owner, inputs, endpoint, identity, expected):
    run = accepted(owner, inputs)
    client = Client()
    if identity == "approver":
        client.force_login(inputs[3].user)
    elif identity == "recovery":
        client = owner[0]
        owner[2].recovery_restricted = True
        owner[2].save(update_fields=["recovery_restricted"])
    response = (
        client.post(paths(run)[endpoint], data="{}", content_type="application/json")
        if endpoint == 2
        else client.get(paths(run)[endpoint])
    )
    assert response.status_code == expected


@pytest.mark.parametrize(
    "raw,content_type", [("", "application/octet-stream"), ("{}", "application/json")]
)
def test_cancel_is_idempotent_and_returns_status_schema(owner, inputs, raw, content_type):
    run = accepted(owner, inputs)
    first = owner[0].post(paths(run)[2], data=raw, content_type=content_type)
    second = owner[0].post(paths(run)[2], data=raw, content_type=content_type)
    assert first.status_code == second.status_code == 202
    validate(first.json(), "hermes-status.schema.json")
    assert first.json() == second.json()
    assert first.json()["cancel_requested"] is True
    assert run.events.filter(event_type="cancel_requested").count() == 1


@pytest.mark.parametrize(
    "raw,content_type",
    [
        ("[]", "application/json"),
        ('{"extra":"private-content"}', "application/json"),
        ("{}", "text/plain"),
        ("{", "application/json"),
        (" " * 1025, "application/json"),
    ],
)
def test_cancel_rejects_nonempty_authority_or_unbounded_body(owner, inputs, raw, content_type):
    run = accepted(owner, inputs)
    response = owner[0].post(paths(run)[2], data=raw, content_type=content_type)
    assert response.status_code == 400
    assert "private-content" not in response.content.decode()
    run.control.refresh_from_db()
    assert run.control.cancel_requested_at is None


def test_legacy_run_detail_schema_survives_and_new_endpoints_are_404(owner, inputs):
    run = create_run(workflow=inputs[0])
    detail = owner[0].get(paths(run)[0])
    assert detail.status_code == 200
    validate(detail.json(), "run-read.schema.json")
    assert owner[0].get(paths(run)[1]).status_code == 404
    assert (
        owner[0].post(paths(run)[2], data="{}", content_type="application/json").status_code == 404
    )


def test_authorized_legacy_reviewer_access_remains_available(inputs):
    run = create_run(workflow=inputs[0])
    client = Client()
    client.force_login(inputs[3].user)
    response = client.get(paths(run)[0])
    assert response.status_code == 200
    validate(response.json(), "run-read.schema.json")


def pending_run(owner, inputs, *, count=2):
    run = accepted(owner, inputs)
    binding = run.hermes_binding
    capability = WorkflowCapability.objects.create(
        token_digest="a" * 64,
        workflow=run.workflow,
        revision=run.event_revision,
        revision_digest=binding.revision_digest,
        actor=binding.actor,
        correlation_id=binding.correlation_id,
        tools=[],
        expires_at=timezone.now() + timedelta(seconds=120),
        revoked_at=timezone.now(),
    )
    run.control.capability = capability
    run.control.save(update_fields=["capability", "updated_at"])
    proposal = MCPSubmission.objects.create(
        capability=capability,
        kind="proposal",
        content={"private-copy": "private-content"},
        digest="b" * 64,
    )
    for index in range(count):
        DraftOperation.objects.create(
            workflow=run.workflow,
            revision=run.event_revision,
            actor=binding.actor,
            proposal=proposal,
            provider="eventbrite" if index % 2 == 0 else "iterable",
            operation_kind="create_eventbrite_draft"
            if index % 2 == 0
            else "create_iterable_email_draft",
            action_digest="c" * 64,
            idempotency_key=f"{index:064x}",
        )
    AgentRun.objects.filter(pk=run.id).update(
        status="succeeded", started_at=timezone.now(), finished_at=timezone.now()
    )
    run.refresh_from_db()
    return run, capability, proposal


def test_pending_results_are_inert_bound_content_free_and_capped(owner, inputs):
    run, _, _ = pending_run(owner, inputs, count=21)
    response = owner[0].get(paths(run)[1])
    assert response.status_code == 200
    validate(response.json(), "pending-operation-page.schema.json")
    assert len(response.json()["results"]) == 20
    assert "private-content" not in response.content.decode()
    assert all(item["status"] == "pending" for item in response.json()["results"])
    assert not DraftOperation.objects.exclude(approval=None, receipt=None).exists()
    status = owner[0].get(paths(run)[0])
    validate(status.json(), "hermes-status.schema.json")
    assert status.json()["pending_operation_count"] == 20


def test_operations_from_other_capability_never_appear(owner, inputs):
    run, capability, proposal = pending_run(owner, inputs)
    foreign = WorkflowCapability.objects.create(
        token_digest="d" * 64,
        workflow=run.workflow,
        revision=run.event_revision,
        revision_digest=capability.revision_digest,
        actor=capability.actor,
        correlation_id=uuid.uuid4(),
        tools=[],
        expires_at=capability.expires_at,
    )
    proposal.capability = foreign
    proposal.save(update_fields=["capability"])
    response = owner[0].get(paths(run)[1])
    assert response.status_code == 200
    assert response.json()["results"] == []


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_operations_remain_hidden_until_accepted_success(owner, inputs, status):
    run, _, _ = pending_run(owner, inputs)
    AgentRun.objects.filter(pk=run.id).update(status=status)
    response = owner[0].get(paths(run)[1])
    assert response.status_code == 200
    assert response.json()["results"] == []


@pytest.mark.parametrize("binding", ["workflow", "revision"])
def test_same_capability_wrong_operation_binding_is_hidden(owner, inputs, binding):
    from tests.agents.test_runs import create_workflow

    run, _, _ = pending_run(owner, inputs)
    foreign_workflow, foreign_revision, _, _ = create_workflow()
    changes = (
        {"workflow": foreign_workflow} if binding == "workflow" else {"revision": foreign_revision}
    )
    DraftOperation.objects.filter(workflow=run.workflow).update(**changes)
    response = owner[0].get(paths(run)[1])
    assert response.status_code == 200
    assert response.json()["results"] == []
