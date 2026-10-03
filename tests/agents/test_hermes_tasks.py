import hashlib
import uuid
from datetime import timedelta

import pytest
from agents import tasks
from agents.models import AgentRun, MCPSubmission, WorkflowCapability
from agents.tool_schemas import bounded_json
from django.utils import timezone
from launchloop.engine import prepare_package
from launchloop.models import DemoActor, Workflow
from launchloop.services import NEW_YORK_EVENT, package_hash

from tests.agents.test_budgets import create_policy, create_profile
from tests.agents.test_runs import create_workflow

pytestmark = pytest.mark.django_db


@pytest.fixture
def inputs(settings, monkeypatch):
    settings.CIVICLOOP_HERMES_ENABLED = True
    settings.CIVICLOOP_HERMES_PROFILE_ID = "hermes_test"
    settings.CIVICLOOP_HERMES_PROFILE_REVISION = 1
    settings.CIVICLOOP_HERMES_TIMEOUT_SECONDS = 120
    settings.CIVICLOOP_HERMES_MAX_INFERENCES = 8
    profile = create_profile(profile_id="hermes_test")
    create_policy(profile)
    workflow, revision, actor, approver = create_workflow()
    snapshot = dict(
        NEW_YORK_EVENT,
        venue_name="Synthetic venue",
        venue_address="1 Test St",
        access_instructions="Enter front door",
    )
    revision = type(revision).objects.create(
        event=workflow.event, version=2, snapshot=snapshot, author=actor
    )
    workflow.revision = revision
    workflow.package = prepare_package(snapshot)
    workflow.package_hash = package_hash(workflow.package)
    workflow.status = Workflow.Status.READY_FOR_REVIEW
    workflow.save()
    monkeypatch.setattr(tasks.execute_hermes_run, "delay", lambda *args: None)
    return workflow, revision, actor, approver


def queue(inputs):
    workflow, revision, actor, _ = inputs
    return tasks.queue_hermes_run(
        workflow_id=workflow.id, revision_id=revision.id, actor_slug=actor.slug
    )


class FakeClient:
    def __init__(self, behavior=None, cleanup=True):
        self.behavior = behavior
        self.cleanup = cleanup
        self.calls = 0

    def execute(self, run, *, capability, should_cancel):
        self.calls += 1
        if self.behavior:
            self.behavior(run, should_cancel)
        cap = WorkflowCapability.objects.get(correlation_id=run.hermes_binding.correlation_id)
        content = {
            "event_copy": "Synthetic copy",
            "invitation": {"subject": "Invitation", "body": "Body"},
            "reminder": {"subject": "Reminder", "body": "Body"},
            "social": {"body": "Social"},
        }
        proposal = MCPSubmission.objects.create(
            capability=cap,
            kind="proposal",
            content=content,
            digest=hashlib.sha256(bounded_json(content).encode()).hexdigest(),
        )
        return {
            "schema_version": "1.0",
            "run_id": str(run.id),
            "workflow_id": str(run.workflow_id),
            "revision_id": run.event_revision_id,
            "status": "succeeded",
            "failure_category": None,
            "proposal_references": [
                {
                    "proposal_id": str(proposal.id),
                    "proposal_digest": proposal.digest,
                    "schema_id": tasks.PROPOSAL_SCHEMA_ID,
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50, "cost_microusd": 200},
        }

    def cancel(self, run):
        return self.cleanup


def install(monkeypatch, client):
    from agents.hermes import HermesClient

    monkeypatch.setattr(HermesClient, "from_settings", lambda: client)


def test_authorized_queue_and_global_admission(inputs):
    run = queue(inputs)
    assert run.status == "queued"
    assert run.id == uuid.uuid5(
        uuid.NAMESPACE_URL, "urn:civicloop:run:" + str(run.hermes_binding.correlation_id)
    )
    with pytest.raises(tasks.HermesAdmissionDenied):
        queue(inputs)


@pytest.mark.parametrize("reason", ["gate", "inactive", "role", "revision", "package", "not_ready"])
def test_admission_fails_closed(inputs, settings, reason):
    workflow, revision, actor, _ = inputs
    if reason == "gate":
        settings.CIVICLOOP_HERMES_ENABLED = False
    elif reason == "inactive":
        actor.user.is_active = False
        actor.user.save()
    elif reason == "role":
        actor.role = DemoActor.Role.APPROVER
        actor.save()
    elif reason == "revision":
        revision.id += 1
    elif reason == "package":
        workflow.package_hash = "a" * 64
        workflow.save()
    elif reason == "not_ready":
        workflow.status = Workflow.Status.NEEDS_INPUT
        workflow.save()
    with pytest.raises(tasks.HermesAdmissionDenied):
        queue(inputs)
    assert not AgentRun.objects.exists()


def test_success_capability_scope_and_terminal_retry(inputs, monkeypatch):
    client = FakeClient()
    install(monkeypatch, client)
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "succeeded"
    cap = WorkflowCapability.objects.get(correlation_id=run.hermes_binding.correlation_id)
    assert cap.revoked_at
    assert cap.expires_at - cap.issued_at <= timedelta(seconds=300)
    assert set(cap.tools) == set(tasks.TOOLS)
    tasks.execute_hermes_run(str(run.id))
    assert client.calls == 1
    assert list(run.events.values_list("sequence", flat=True)) == [1, 2, 3]


@pytest.mark.parametrize(
    "change, expected",
    [
        ("cancel", "cancelled"),
        ("gate", "cancelled"),
        ("revision", "invalid_output"),
        ("lease", "timeout"),
    ],
)
def test_late_result_cannot_win(inputs, monkeypatch, settings, change, expected):
    def behavior(run, should_cancel):
        if change == "cancel":
            tasks.cancel_hermes_run(run.id)
        elif change == "gate":
            settings.CIVICLOOP_HERMES_ENABLED = False
        elif change == "revision":
            Workflow.objects.filter(pk=run.workflow_id).update(package_hash="c" * 64)
        else:
            type(run.control).objects.filter(run=run).update(
                lease_expires_at=timezone.now() - timedelta(seconds=1)
            )

    install(monkeypatch, FakeClient(behavior))
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.failure_category == expected
    assert run.status != "succeeded"


def test_cleanup_ambiguity_blocks_future_admission(inputs, monkeypatch):
    def behavior(run, should_cancel):
        raise RuntimeError("must never enter durable events")

    install(monkeypatch, FakeClient(behavior, cleanup=False))
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    assert tasks.HermesAdmissionLane.objects.get(pk=1).admission_disabled
    with pytest.raises(tasks.HermesAdmissionDenied):
        queue(inputs)
    assert "must never" not in str(list(run.events.values()))


def test_queued_cancel_never_dispatches(inputs, monkeypatch):
    client = FakeClient()
    install(monkeypatch, client)
    run = queue(inputs)
    tasks.cancel_hermes_run(run.id)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "cancelled"
    assert client.calls == 0


@pytest.mark.parametrize(
    "tamper", ["foreign_capability", "wrong_schema", "wrong_digest", "bad_content", "cost"]
)
def test_invalid_proposals_fail_and_revoke(inputs, monkeypatch, tamper):
    client = FakeClient()
    original = client.execute

    def execute(run, **kwargs):
        result = original(run, **kwargs)
        reference = result["proposal_references"][0]
        if tamper == "foreign_capability":
            cap = WorkflowCapability.objects.get(correlation_id=run.hermes_binding.correlation_id)
            other = WorkflowCapability.objects.create(
                token_digest="f" * 64,
                revision_digest=cap.revision_digest,
                workflow=cap.workflow,
                revision=cap.revision,
                actor=cap.actor,
                tools=cap.tools,
                expires_at=cap.expires_at,
            )
            MCPSubmission.objects.filter(pk=reference["proposal_id"]).update(capability=other)
        elif tamper == "wrong_schema":
            reference["schema_id"] = "urn:civicloop:schema:unrelated:v1.0"
        elif tamper == "wrong_digest":
            reference["proposal_digest"] = "a" * 64
        elif tamper == "bad_content":
            MCPSubmission.objects.filter(pk=reference["proposal_id"]).update(
                content={"unexpected": "field"}
            )
        else:
            result["usage"]["cost_microusd"] = 1
        return result

    client.execute = execute
    install(monkeypatch, client)
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.failure_category == "invalid_output"
    assert run.status == "failed"
    assert WorkflowCapability.objects.get(
        correlation_id=run.hermes_binding.correlation_id
    ).revoked_at


def test_running_redelivery_is_quarantined_and_charged(inputs, monkeypatch):
    from agents.models import BudgetReservation

    run = queue(inputs)
    run.status = "running"
    run.started_at = timezone.now()
    run.save()
    client = FakeClient()
    install(monkeypatch, client)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "failed"
    assert client.calls == 0
    assert tasks.HermesAdmissionLane.objects.get(pk=1).admission_disabled
    reservation = BudgetReservation.objects.get(run_id=run.id)
    assert reservation.settled_cost_microusd == reservation.reserved_cost_microusd


def test_budget_failure_prevents_queue(inputs):
    from agents.models import BudgetPeriod

    BudgetPeriod.objects.create(
        month=timezone.now().date().replace(day=1),
        limit_microusd=25_000_000,
        settled_microusd=25_000_000,
    )
    with pytest.raises(tasks.HermesAdmissionDenied):
        queue(inputs)
    assert not AgentRun.objects.exists()


def test_database_blocks_second_active_hermes_even_without_service(inputs):
    from django.db import IntegrityError, transaction

    from tests.agents.test_runs import create_run

    queue(inputs)
    with pytest.raises(IntegrityError), transaction.atomic():
        another = create_run()
        AgentRun.objects.filter(pk=another.pk).update(hermes_lane=True)


def test_dispatch_failure_releases_unspent_budget(
    inputs, monkeypatch, django_capture_on_commit_callbacks
):
    from agents.models import BudgetReservation

    def unavailable(*args):
        raise RuntimeError("transport secret unsafe")

    monkeypatch.setattr(tasks.execute_hermes_run, "delay", unavailable)
    with django_capture_on_commit_callbacks(execute=True):
        run = queue(inputs)
    run.refresh_from_db()
    assert run.status == "failed"
    assert BudgetReservation.objects.get(run_id=run.id).status == "released"
    assert tasks.HermesAdmissionLane.objects.get(pk=1).active_run_id is None


def test_revocation_failure_persistently_closes_admission(inputs, monkeypatch):
    install(monkeypatch, FakeClient())

    def uncertain(**kwargs):
        raise RuntimeError("must not be logged")

    monkeypatch.setattr(tasks, "revoke_workflow_capability", uncertain)
    monkeypatch.setattr(tasks, "_revoke_record", lambda *args: uncertain())
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.control.admission_disabled
    assert tasks.HermesAdmissionLane.objects.get(pk=1).admission_disabled


def test_pending_operations_are_validated_with_broker_digests(inputs, monkeypatch):
    from agents.mcp import _execute
    from agents.models import DraftOperation

    client = FakeClient()
    original = client.execute

    def execute(run, **kwargs):
        result = original(run, **kwargs)
        proposal = MCPSubmission.objects.get(pk=result["proposal_references"][0]["proposal_id"])
        _execute(
            "request_eventbrite_draft",
            {"proposal_id": str(proposal.id)},
            proposal.capability,
            run.workflow,
        )
        _execute(
            "request_iterable_drafts",
            {"proposal_id": str(proposal.id)},
            proposal.capability,
            run.workflow,
        )
        return result

    client.execute = execute
    install(monkeypatch, client)
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "succeeded"
    assert DraftOperation.objects.count() == 3
    assert set(DraftOperation.objects.values_list("status", flat=True)) == {"pending"}
    assert not DraftOperation.objects.exclude(approval=None, receipt=None).exists()


def test_expired_running_budget_is_preserved_then_charged(inputs, monkeypatch):
    from agents.budgets import expire_reservations
    from agents.models import BudgetReservation

    run = queue(inputs)
    run.status = "running"
    run.started_at = timezone.now()
    run.save()
    BudgetReservation.objects.filter(run_id=run.id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert expire_reservations() == 0
    assert BudgetReservation.objects.get(run_id=run.id).status == "reserved"
    install(monkeypatch, FakeClient())
    tasks.execute_hermes_run(str(run.id))
    reservation = BudgetReservation.objects.get(run_id=run.id)
    assert reservation.status == "settled"
    assert reservation.settled_cost_microusd == reservation.reserved_cost_microusd


def test_cancel_running_revokes_before_return(inputs, monkeypatch):
    def behavior(run, should_cancel):
        tasks.cancel_hermes_run(run.id)
        assert WorkflowCapability.objects.get(pk=run.control.capability_id).revoked_at is not None
        assert should_cancel()

    install(monkeypatch, FakeClient(behavior))
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "cancelled"


def test_ambiguous_ledger_failure_keeps_reservation_and_quarantine(inputs, monkeypatch):
    from agents.models import BudgetReservation

    def behavior(*args):
        raise RuntimeError("ambiguous billable response")

    install(monkeypatch, FakeClient(behavior, cleanup=False))

    def accounting_unavailable(**kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(tasks, "charge_reserved_budget", accounting_unavailable)
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.control.admission_disabled
    assert tasks.HermesAdmissionLane.objects.get(pk=1).admission_disabled
    assert BudgetReservation.objects.get(run_id=run.id).status == "reserved"
    from agents.budgets import expire_reservations

    assert expire_reservations(now=timezone.now() + timedelta(days=1)) == 0
    assert BudgetReservation.objects.get(run_id=run.id).status == "reserved"


@pytest.mark.django_db(transaction=True)
def test_postgres_parallel_admission_has_one_winner(inputs):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock concurrency requires PostgreSQL test database.")
    tasks.HermesAdmissionLane.objects.get_or_create(pk=1)
    barrier = Barrier(2)
    workflow_id, revision_id, actor_slug = inputs[0].id, inputs[1].id, inputs[2].slug

    def admit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                tasks.queue_hermes_run(
                    workflow_id=workflow_id, revision_id=revision_id, actor_slug=actor_slug
                )
                return "accepted"
            except tasks.HermesAdmissionDenied:
                return "denied"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: admit(), range(2)))
    assert sorted(results) == ["accepted", "denied"]
    assert AgentRun.objects.filter(hermes_lane=True, status="queued").count() == 1


def test_revocation_error_quarantines_even_if_cleanup_retry_succeeds(inputs, monkeypatch):
    install(monkeypatch, FakeClient())

    def failed(**kwargs):
        raise RuntimeError("revocation uncertain")

    monkeypatch.setattr(tasks, "revoke_workflow_capability", failed)
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.control.admission_disabled
    assert WorkflowCapability.objects.get(pk=run.control.capability_id).revoked_at


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("ancillary", [False, True])
@pytest.mark.parametrize("action", ["execute", "cancel"])
def test_other_agent_lane_is_untouched(inputs, monkeypatch, status, ancillary, action):
    from agents.budgets import reserve_budget
    from agents.models import AgentRunControl, BudgetReservation, HermesRunBinding

    from tests.agents.test_runs import create_run

    run = create_run(workflow=inputs[0])
    run.status = status
    run.started_at = timezone.now() if status == "running" else None
    run.save()
    if ancillary:
        HermesRunBinding.objects.create(run=run, actor=inputs[2])
        AgentRunControl.objects.create(run=run)
    reserve_budget(
        run_id=run.id,
        profile_id=run.model_profile.profile_id,
        profile_revision=run.model_profile.revision,
        estimated_input_tokens=10,
        estimated_output_tokens=10,
    )
    lane = tasks.HermesAdmissionLane.objects.get_or_create(pk=1)[0]
    previous_lane = (lane.active_run_id, lane.admission_disabled)
    client = FakeClient()
    install(monkeypatch, client)
    if action == "execute":
        tasks.execute_hermes_run(str(run.id))
    else:
        tasks.cancel_hermes_run(run.id)
    run.refresh_from_db()
    lane.refresh_from_db()
    assert run.status == status and run.finished_at is None and run.failure_category == ""
    assert client.calls == 0
    assert not run.events.exists()
    assert (lane.active_run_id, lane.admission_disabled) == previous_lane
    assert BudgetReservation.objects.get(run_id=run.id).status == "reserved"
    if ancillary:
        assert run.control.cancel_requested_at is None
        assert run.control.admission_disabled is False


@pytest.mark.parametrize("action", ["execute", "cancel", "finish"])
def test_missing_hermes_control_quarantines_without_cleanup_error(inputs, monkeypatch, action):
    from agents.models import AgentRunControl, BudgetReservation

    run = queue(inputs)
    AgentRunControl.objects.filter(run=run).delete()
    client = FakeClient()
    install(monkeypatch, client)
    if action == "execute":
        tasks.execute_hermes_run(str(run.id))
    elif action == "cancel":
        tasks.cancel_hermes_run(run.id)
    else:
        tasks._finish(run.id, "dependency_unavailable", billable=True, cleanup_ok=False)
    run.refresh_from_db()
    assert run.status == "queued"
    assert client.calls == 0
    lane = tasks.HermesAdmissionLane.objects.get(pk=1)
    assert lane.admission_disabled and lane.active_run_id == run.id
    assert BudgetReservation.objects.get(run_id=run.id).status == "reserved"
    with pytest.raises(tasks.HermesAdmissionDenied):
        queue(inputs)
