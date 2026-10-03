import uuid

import pytest
from agents import tasks
from agents.models import AgentRun, HermesStartReceipt
from django.db import IntegrityError, transaction
from django.utils import timezone
from launchloop.models import AuditEvent

from tests.agents.test_hermes_tasks import inputs as inputs
from tests.agents.test_runs import create_workflow

pytestmark = pytest.mark.django_db


def start(inputs, key, **overrides):
    workflow, revision, actor, _ = inputs
    return tasks.start_hermes_run(
        **{
            "idempotency_key": key,
            "workflow_id": workflow.id,
            "revision_id": revision.id,
            "actor_slug": actor.slug,
            **overrides,
        }
    )


def test_receipt_persists_before_only_dispatch_and_exact_replay(
    inputs, monkeypatch, django_capture_on_commit_callbacks
):
    key = uuid.uuid4()
    dispatched = []

    def dispatch(run_id):
        receipt = HermesStartReceipt.objects.get(pk=key)
        assert str(receipt.run_id) == run_id
        dispatched.append(run_id)

    monkeypatch.setattr(tasks.execute_hermes_run, "delay", dispatch)
    with django_capture_on_commit_callbacks(execute=True):
        first = start(inputs, key)
        replay = start(inputs, key)
    assert replay.id == first.id
    assert dispatched == [str(first.id)]
    assert AgentRun.objects.count() == HermesStartReceipt.objects.count() == 1


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_terminal_replay_survives_gate_and_current_package_change(inputs, settings, status):
    key = uuid.uuid4()
    first = start(inputs, key)
    first.status = status
    first.started_at = timezone.now()
    first.finished_at = timezone.now()
    first.failure_category = (
        "" if status == "succeeded" else ("cancelled" if status == "cancelled" else "timeout")
    )
    first.save()
    settings.CIVICLOOP_HERMES_ENABLED = False
    workflow = inputs[0]
    workflow.package_hash = "a" * 64
    workflow.save()
    assert start(inputs, key).id == first.id
    assert AgentRun.objects.count() == 1


@pytest.mark.parametrize("different", ["actor", "workflow", "revision", "owner"])
def test_same_key_with_different_bound_request_conflicts(inputs, different):
    key = uuid.uuid4()
    first = start(inputs, key)
    kwargs = {}
    if different == "actor":
        _, _, another, _ = create_workflow()
        kwargs["actor_slug"] = another.slug
    elif different == "workflow":
        kwargs["workflow_id"] = uuid.uuid4()
    elif different == "revision":
        kwargs["revision_id"] = inputs[1].id + 1
    else:
        actor = inputs[2]
        from django.contrib.auth.models import User

        actor.user = User.objects.create(username="replacement-owner")
        actor.save()
    with pytest.raises(tasks.HermesStartConflict):
        start(inputs, key, **kwargs)
    assert HermesStartReceipt.objects.get(pk=key).run_id == first.id
    assert AgentRun.objects.count() == 1


def test_receipt_failure_rolls_back_run_budget_and_dispatch(
    inputs, monkeypatch, django_capture_on_commit_callbacks
):
    from agents.models import BudgetReservation

    def fail(*args, **kwargs):
        raise IntegrityError("synthetic receipt write failure")

    monkeypatch.setattr(HermesStartReceipt.objects, "create", fail)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(tasks.HermesAdmissionDenied):
            start(inputs, uuid.uuid4())
    assert callbacks == []
    assert AgentRun.objects.count() == BudgetReservation.objects.count() == 0


def test_new_key_cannot_bypass_lane(inputs):
    start(inputs, uuid.uuid4())
    with pytest.raises(tasks.HermesAdmissionDenied):
        start(inputs, uuid.uuid4())
    assert HermesStartReceipt.objects.count() == 1


def test_receipt_is_immutable_and_key_unique(inputs):
    key = uuid.uuid4()
    start(inputs, key)
    receipt = HermesStartReceipt.objects.get(pk=key)
    receipt.revision_id += 1
    with pytest.raises(ValueError, match="immutable"):
        receipt.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        HermesStartReceipt.objects.bulk_create(
            [
                HermesStartReceipt(
                    id=key,
                    owner=receipt.owner,
                    actor=receipt.actor,
                    workflow=receipt.workflow,
                    revision_id=inputs[1].id,
                    run=receipt.run,
                )
            ]
        )


@pytest.mark.parametrize("key", ["not-a-uuid", None, 17])
def test_required_uuid_key_and_unauthorized_actor_fail_without_receipts(inputs, key):
    with pytest.raises(tasks.HermesAdmissionDenied):
        start(inputs, key)
    assert not HermesStartReceipt.objects.exists()


def test_deactivated_actor_cannot_replay(inputs):
    key = uuid.uuid4()
    start(inputs, key)
    actor = inputs[2]
    actor.user.is_active = False
    actor.user.save()
    with pytest.raises(tasks.HermesAdmissionDenied):
        start(inputs, key)


def test_running_cancel_replay_does_not_duplicate_evidence(inputs):
    run = start(inputs, uuid.uuid4())
    run.status = "running"
    run.started_at = timezone.now()
    run.save()
    tasks.cancel_hermes_run(run.id)
    first_events = list(run.events.values_list("sequence", "event_type", "outcome"))
    first_audits = AuditEvent.objects.filter(action="hermes.cancel_requested").count()
    run.control.refresh_from_db()
    first_timestamp = run.control.cancel_requested_at
    tasks.cancel_hermes_run(run.id)
    assert list(run.events.values_list("sequence", "event_type", "outcome")) == first_events
    assert AuditEvent.objects.filter(action="hermes.cancel_requested").count() == first_audits
    run.control.refresh_from_db()
    assert run.control.cancel_requested_at == first_timestamp


@pytest.mark.django_db(transaction=True)
def test_postgres_concurrent_same_key_returns_one_run_and_dispatch(inputs, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection

    if connection.vendor != "postgresql":
        pytest.skip("Receipt concurrency requires PostgreSQL row locking.")
    tasks.HermesAdmissionLane.objects.get_or_create(pk=1)
    key = uuid.uuid4()
    barrier = Barrier(2)
    dispatches = []
    monkeypatch.setattr(tasks.execute_hermes_run, "delay", lambda run_id: dispatches.append(run_id))

    def admit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return start(inputs, key).id
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(lambda _: admit(), range(2)))
    assert len(set(run_ids)) == 1
    assert dispatches == [str(run_ids[0])]
    assert HermesStartReceipt.objects.count() == AgentRun.objects.count() == 1


def test_postgres_receipt_raw_update_and_delete_rejected(inputs):
    from django.db import DatabaseError, connection

    if connection.vendor != "postgresql":
        pytest.skip("Receipt immutable database trigger requires PostgreSQL.")
    key = uuid.uuid4()
    start(inputs, key)
    with pytest.raises(DatabaseError), transaction.atomic():
        HermesStartReceipt.objects.filter(pk=key).update(revision_id=inputs[1].id + 1)
    with pytest.raises(DatabaseError), transaction.atomic():
        HermesStartReceipt.objects.filter(pk=key).delete()
    assert HermesStartReceipt.objects.get(pk=key).revision_id == inputs[1].id
