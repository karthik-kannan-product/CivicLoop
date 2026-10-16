import json
import uuid
from datetime import timedelta

import pytest
from agents import tasks
from agents.models import AgentRun
from django.test import Client
from django.utils import timezone
from evaluations.judge import run_fixed_judge
from launchloop.models import Workflow
from launchloop.owner_events import validate_facts
from launchloop.pilot import owner_operator, start_manual_event, update_event_facts
from launchloop.services import run_workflow

from tests.agents.test_budgets import create_policy, create_profile
from tests.agents.test_hermes_tasks import FakeClient, install
from tests.identity.test_security_actions_api import create_authenticated_owner

pytestmark = pytest.mark.django_db
FACTS = {
    "title": "Toronto volunteer breakfast",
    "date": "2026-10-20",
    "start_time": "09:00",
    "end_time": "11:00",
    "timezone": "America/Toronto",
    "city": "Toronto",
    "region": "ON",
    "country": "CA",
    "venue_name": "Community hall",
    "venue_address": "100 Test Street",
    "access_instructions": "Use the accessible entrance",
    "signup_url": "https://example.test/volunteer-breakfast",
}


@pytest.fixture
def owner_lane(settings, monkeypatch):
    settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED = True
    settings.CIVICLOOP_HERMES_ENABLED = True
    settings.CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED = True
    settings.CIVICLOOP_HERMES_PROFILE_ID = "owner_test"
    settings.CIVICLOOP_HERMES_PROFILE_REVISION = 1
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    profile = create_profile(profile_id="owner_test")
    create_policy(profile)
    client, _, session, _ = create_authenticated_owner()
    actor = owner_operator(session)
    monkeypatch.setattr(tasks.execute_hermes_run, "delay", lambda *args: None)
    return client, session, actor


def ready(owner_lane):
    workflow = start_manual_event(FACTS, owner_lane[2])
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    return workflow


def queue(owner_lane, workflow):
    return tasks.queue_hermes_run(
        workflow_id=workflow.pk,
        revision_id=workflow.revision_id,
        actor_slug=owner_lane[2].pk,
        owner_session_id=owner_lane[1].pk,
    )


def test_owner_manual_http_full_facts_to_minimized_queue(owner_lane):
    client = owner_lane[0]
    response = client.post(
        "/api/v1/events/manual", data=json.dumps(FACTS), content_type="application/json"
    )
    assert response.status_code == 200
    state = response.json()
    from tests.api_contracts.test_openapi_parity import validate

    validate(state, "launchloop/demo-state.schema.json")
    assert state["event"]["revision"]["source_kind"] == "manual"
    workflow_id = state["workflow"]["id"]
    response = client.post(f"/api/v1/workflows/{workflow_id}/runs")
    assert response.status_code == 200
    validate(response.json(), "launchloop/demo-state.schema.json")
    package = response.json()["workflow"]["package"]
    assert package["schema_id"] == "owner_event_draft_v1"
    assert package["status"] == "ready_for_review"
    assert package["audience"]["id"] is None
    assert package["sponsor"]["passed"] is False
    revision_id = response.json()["event"]["revision"]["id"]
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/hermes-runs",
        data=json.dumps({"revision_id": revision_id}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 202
    run = AgentRun.objects.get(pk=response.json()["run_id"])
    assert run.privacy_mode == "pilot_minimized"
    assert (
        run.fixture_manifest_id,
        run.fixture_manifest_revision,
        run.fixture_manifest_digest,
    ) == (
        None,
        None,
        None,
    )
    assert run.hermes_binding.owner_session_id == owner_lane[1].pk
    assert "synthetic" not in run.event_revision.snapshot


def test_owner_facts_http_and_missing_workflow(owner_lane):
    client = owner_lane[0]
    workflow = start_manual_event(FACTS, owner_lane[2])
    response = client.post(
        f"/api/v1/workflows/{workflow.pk}/facts",
        data=json.dumps({"city": "Ottawa"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    from tests.api_contracts.test_openapi_parity import validate

    validate(response.json(), "launchloop/demo-state.schema.json")
    workflow.refresh_from_db()
    assert workflow.revision.snapshot["city"] == "Ottawa"
    assert workflow.revision.snapshot["title"] == FACTS["title"]
    assert client.post(f"/api/v1/workflows/{uuid.uuid4()}/runs").status_code == 404


@pytest.mark.parametrize("source", ["manual", "eventbrite"])
def test_sandbox_answers_cannot_revise_owner_event(owner_lane, source):
    from launchloop.models import DemoActor, EventRevision
    from launchloop.pilot import refresh_eventbrite_events, select_eventbrite_event
    from launchloop.services import reset_demo

    from tests.launchloop.test_eventbrite_pilot import FakeEventbriteReader, event

    if source == "manual":
        brief = {key: FACTS[key] for key in ("title", "date", "timezone")}
        workflow = start_manual_event(brief, owner_lane[2])
    else:
        source_id = refresh_eventbrite_events(reader=FakeEventbriteReader((event(),)))[0]["id"]
        workflow = select_eventbrite_event(source_id, owner_lane[2])
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    assert workflow.status == Workflow.Status.NEEDS_INPUT
    original_revision = workflow.revision_id
    original_hash = workflow.package_hash
    original_source = workflow.revision.source_snapshot_id
    revision_count = EventRevision.objects.filter(event=workflow.event).count()
    reset_demo()
    sandbox = Client()
    sandbox.force_login(DemoActor.objects.get(pk="maya").user)
    response = sandbox.post(
        f"/api/v1/workflows/{workflow.pk}/answers",
        data=json.dumps({"venue_name": "Unauthorized replacement"}),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "synthetic_only"
    workflow.refresh_from_db()
    assert workflow.revision_id == original_revision
    assert workflow.package_hash == original_hash
    assert workflow.revision.author_id == owner_lane[2].pk
    assert workflow.revision.source_snapshot_id == original_source
    assert EventRevision.objects.filter(event=workflow.event).count() == revision_count
    response = owner_lane[0].post(
        f"/api/v1/workflows/{workflow.pk}/facts",
        data=json.dumps({"venue_name": "Owner confirmed venue"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    workflow.refresh_from_db()
    assert workflow.revision.snapshot["venue_name"] == "Owner confirmed venue"
    assert workflow.revision.author_id == owner_lane[2].pk
    assert workflow.revision.source_snapshot_id == original_source


def test_owner_worker_success_and_no_provider_execution(owner_lane, monkeypatch):
    run = queue(owner_lane, ready(owner_lane))
    install(monkeypatch, FakeClient())
    tasks.execute_hermes_run(str(run.pk))
    run.refresh_from_db()
    assert run.status == "succeeded"
    assert run.control.capability.revoked_at is not None
    from integrations.models import DraftExecution

    assert not DraftExecution.objects.exists()


@pytest.mark.parametrize("incomplete", [False, True])
def test_owner_readiness_does_not_emit_synthetic_evaluation(owner_lane, incomplete):
    from observability.runtime import TelemetryConfig, build_runtime, set_runtime_for_testing

    from tests.observability.test_launchloop_tracing import CaptureExporter

    exporter = CaptureExporter()
    runtime = build_runtime(TelemetryConfig(enabled=True, synchronous=True), exporter=exporter)
    set_runtime_for_testing(runtime)
    try:
        facts = {key: FACTS[key] for key in ("title", "date", "timezone")} if incomplete else FACTS
        workflow = start_manual_event(facts, owner_lane[2])
        run_workflow(workflow.pk, owner_lane[2])
        runtime.force_flush()
        assert "launchloop.deterministic_lane" in {span.name for span in exporter.spans}
        assert "launchloop.evaluation" not in {span.name for span in exporter.spans}
    finally:
        set_runtime_for_testing(None)


@pytest.mark.parametrize("lane", ["synthetic", "owner"])
def test_mcp_review_requirements_match_source(owner_lane, settings, tmp_path, lane):
    from agents.capabilities import issue_workflow_capability
    from agents.mcp import dispatch_mcp_tool

    from tests.agents.test_runs import create_workflow

    workflow = ready(owner_lane) if lane == "owner" else create_workflow()[0]
    identity = "test-source-policy-service-identity-000000"
    identity_path = tmp_path / "mcp-service-identity"
    identity_path.write_text(identity)
    settings.CIVICLOOP_MCP_TOKEN_FILE = str(identity_path)
    token = issue_workflow_capability(
        workflow_id=workflow.pk,
        revision_id=workflow.revision_id,
        actor_id=workflow.revision.author_id,
        tools=frozenset({"get_policy_context", "propose_campaign_drafts", "validate_proposal"}),
        lifetime_seconds=60,
    )

    correlation_id = str(uuid.uuid4())

    def invoke(tool, **extra):
        return dispatch_mcp_tool(
            tool_name=tool,
            service_identity=identity,
            capability=token,
            arguments={
                "workflow_id": str(workflow.pk),
                "revision_id": workflow.revision_id,
                "actor_id": workflow.revision.author_id,
                "correlation_id": correlation_id,
                "request_id": str(uuid.uuid4()),
                "idempotency_key": str(uuid.uuid4()),
                **extra,
            },
        )

    context = invoke("get_policy_context")
    assert context["provider_execution_allowed"] is False
    assert context["approval_required"] == (
        "four_eyes_exact_revision_and_action_digest"
        if lane == "synthetic"
        else "owner_exact_revision_and_action_digest_audience_policy_review"
    )
    proposed = invoke("propose_campaign_drafts", proposal={
        "event_copy": "Draft",
        "invitation": {"subject": "Invite", "body": "Copy"},
        "reminder": {"subject": "Reminder", "body": "Copy"},
        "social": {"body": "Copy"},
    })
    checked = invoke("validate_proposal", proposal_id=proposed["proposal_id"])
    assert checked["execution_authorized"] is False
    assert checked["required_review"] == (
        "deterministic_policy_and_four_eyes"
        if lane == "synthetic"
        else "owner_exact_draft_approval_and_deferred_audience_policy"
    )


@pytest.mark.parametrize("mode", ["revoked", "restricted", "expired", "missing"])
def test_owner_admission_mfa_fails_before_run(owner_lane, mode):
    workflow = ready(owner_lane)
    session = owner_lane[1]
    if mode == "revoked":
        session.revoked_at = timezone.now()
    elif mode == "restricted":
        session.recovery_restricted = True
    elif mode == "expired":
        session.expires_at = timezone.now() - timedelta(seconds=1)
    session.save()
    with pytest.raises(tasks.HermesAdmissionDenied):
        if mode == "missing":
            tasks.queue_hermes_run(
                workflow_id=workflow.pk,
                revision_id=workflow.revision_id,
                actor_slug=owner_lane[2].pk,
            )
        else:
            queue(owner_lane, workflow)
    assert not AgentRun.objects.exists()


@pytest.mark.parametrize("mode", ["cancel", "stale", "revoked", "fixture"])
def test_owner_worker_rechecks_binding_before_model(owner_lane, monkeypatch, mode):
    workflow = ready(owner_lane)
    run = queue(owner_lane, workflow)
    if mode == "cancel":
        run.control.cancel_requested_at = timezone.now()
        run.control.save()
    elif mode == "stale":
        Workflow.objects.filter(pk=workflow.pk).update(package_hash="a" * 64)
    elif mode == "revoked":
        owner_lane[1].revoked_at = timezone.now()
        owner_lane[1].save()
    else:
        # In-memory corrupt provenance still fails the trusted worker readiness predicate.
        run.fixture_manifest_id = "forged"
        assert not tasks._run_ready(run, workflow)
        return
    client = FakeClient()
    install(monkeypatch, client)
    tasks.execute_hermes_run(str(run.pk))
    run.refresh_from_db()
    assert run.status == ("cancelled" if mode == "cancel" else "failed")
    assert client.calls == 0


def test_full_facts_edit_incomplete_and_active_denial(owner_lane):
    workflow = start_manual_event(
        {k: FACTS[k] for k in ("title", "date", "timezone")}, owner_lane[2]
    )
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    assert workflow.package["status"] == "needs_input"
    with pytest.raises(tasks.HermesAdmissionDenied):
        queue(owner_lane, workflow)
    workflow = update_event_facts(workflow.pk, FACTS, owner_lane[2])
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    queue(owner_lane, workflow)
    with pytest.raises(ValueError, match="review_in_progress"):
        update_event_facts(workflow.pk, {"city": "Montreal"}, owner_lane[2])


@pytest.mark.parametrize(
    "field", ["synthetic", "owner_event", "privacy_mode", "fixture_manifest_id"]
)
def test_public_facts_cannot_claim_provenance(field):
    with pytest.raises(ValueError):
        validate_facts({**FACTS, field: "caller value"})


@pytest.mark.parametrize(
    "changes",
    [
        {"end_time": "08:00"},
        {"timezone": "Unknown/Zone"},
        {"signup_url": "https://name:password@example.test"},
        {"date": "2026-11-01", "start_time": "01:30"},
    ],
)
def test_invalid_public_facts_fail_closed(changes):
    with pytest.raises(ValueError):
        validate_facts({**FACTS, **changes})


def test_owner_lane_rejects_fixture_evaluation_and_other_operator(owner_lane):
    workflow = ready(owner_lane)
    with pytest.raises(ValueError, match="evaluation_synthetic_only"):
        run_fixed_judge(workflow, owner_lane[1])
    client = Client()
    response = client.post(
        f"/api/v1/workflows/{workflow.pk}/facts",
        data=json.dumps(FACTS),
        content_type="application/json",
    )
    assert response.status_code in (401, 403)
    response = owner_lane[0].post(f"/api/v1/workflows/{workflow.pk}/submit")
    assert response.status_code == 403

    from launchloop.models import ApprovalRequest, ConnectorExecution
    from launchloop.services import DemoError, decide_approval

    approval = ApprovalRequest.objects.create(
        workflow=workflow,
        submitter=owner_lane[2],
        package_hash=workflow.package_hash,
    )
    with pytest.raises(DemoError) as error:
        decide_approval(approval.pk, owner_lane[2], "approve", workflow.package_hash)
    assert error.value.code == "synthetic_only"
    assert not ConnectorExecution.objects.exists()


def test_agent_run_schema_preserves_lifecycle_and_real_fixture_absence():
    from pathlib import Path

    from jsonschema import Draft202012Validator, ValidationError

    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "schemas/agents/agent-run.schema.json").read_text())
    payload = json.loads(
        (root / "tests/api_contracts/fixtures/observable_agent_contracts/agent_run.valid.json")
        .read_text()
    )
    payload["privacy_mode"] = "pilot_minimized"
    for key in ("fixture_manifest_id", "fixture_manifest_revision", "fixture_manifest_digest"):
        payload[key] = None
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    payload["fixture_manifest_id"] = "false_fixture"
    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_invalid_imported_schedule_remains_editable(owner_lane):
    from launchloop.pilot import refresh_eventbrite_events, select_eventbrite_event

    from tests.launchloop.test_eventbrite_pilot import FakeEventbriteReader, event

    source_id = refresh_eventbrite_events(reader=FakeEventbriteReader((event(),)))[0]["id"]
    workflow = select_eventbrite_event(source_id, owner_lane[2])
    workflow = update_event_facts(workflow.pk, FACTS, owner_lane[2])
    source_snapshot_id = workflow.revision.source_snapshot_id
    workflow.revision.snapshot.update(date="bad", start_time="23:00", end_time="01:30")
    workflow.revision.save()
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    assert workflow.package["status"] == "needs_input"
    # Correct one field while another remains invalid; no impossible all-at-once repair.
    workflow = update_event_facts(workflow.pk, {"date": FACTS["date"]}, owner_lane[2])
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    assert workflow.package["status"] == "needs_input"
    workflow = update_event_facts(workflow.pk, {"start_time": "00:30"}, owner_lane[2])
    run_workflow(workflow.pk, owner_lane[2])
    workflow.refresh_from_db()
    assert workflow.package["status"] == "ready_for_review"
    run = queue(owner_lane, workflow)
    assert run.event_revision.source_snapshot_id == source_snapshot_id
    assert run.privacy_mode == "pilot_minimized"


def test_owner_binding_immutable_and_fixture_constraint(owner_lane):
    from django.db import IntegrityError, OperationalError, connection, transaction

    run = queue(owner_lane, ready(owner_lane))
    binding = run.hermes_binding
    binding.owner_session = None
    with pytest.raises(ValueError, match="immutable"):
        binding.save()
    expected_error = OperationalError if connection.vendor == "postgresql" else IntegrityError
    with pytest.raises(expected_error) as rejected, transaction.atomic():
        AgentRun.objects.filter(pk=run.pk).update(fixture_manifest_revision=3)
    if connection.vendor == "postgresql":
        assert rejected.value.__cause__.sqlstate == "55000"
    run.refresh_from_db()
    assert run.fixture_manifest_revision is None


def test_owner_readiness_cannot_use_another_principal_session(owner_lane):
    from django.contrib.auth.models import User
    from launchloop.models import DemoActor

    workflow = ready(owner_lane)
    other = DemoActor.objects.create(
        slug="other-owner",
        user=User.objects.create_user("other-owner"),
        display_name="Other operator",
        role=DemoActor.Role.OPERATOR,
    )
    with pytest.raises(tasks.HermesAdmissionDenied):
        tasks.queue_hermes_run(
            workflow_id=workflow.pk,
            revision_id=workflow.revision_id,
            actor_slug=other.pk,
            owner_session_id=owner_lane[1].pk,
        )
    assert not AgentRun.objects.exists()


def test_owner_worker_metadata_contains_no_event_content(owner_lane):
    from agents.telemetry import run_attributes

    run = queue(owner_lane, ready(owner_lane))
    attributes = json.dumps(run_attributes(run))
    assert FACTS["title"] not in attributes
    assert FACTS["venue_address"] not in attributes
    assert "fixture" not in attributes


@pytest.mark.django_db(transaction=True)
def test_postgresql_owner_session_binding_bulk_mutation_denied(owner_lane):
    from agents.models import HermesRunBinding
    from django.db import DatabaseError, connection, transaction

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row trigger enforcement requires PostgreSQL.")
    run = queue(owner_lane, ready(owner_lane))
    with pytest.raises(DatabaseError), transaction.atomic():
        HermesRunBinding.objects.filter(run=run).update(owner_session=None)
