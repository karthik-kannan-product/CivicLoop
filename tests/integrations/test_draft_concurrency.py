"""Real PostgreSQL dispatch contention; SQLite cannot verify row-lock semantics."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Barrier, Event, Lock

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, connections
from integrations import draft_operations as service
from integrations.eventbrite_drafts import EventbriteDraftReceipt
from integrations.models import DraftExecution

from tests.integrations import test_draft_operations as draft_tests

pytestmark = pytest.mark.django_db(transaction=True)
accepted = draft_tests.accepted


@pytest.mark.parametrize("ambiguous", [False, True], ids=["confirmed", "unknown"])
def test_postgres_concurrent_dispatch_has_one_committed_claim(accepted, monkeypatch, ambiguous):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL: SQLite cannot establish committed row-lock contention.")

    operation = draft_tests.approve(accepted)
    user = accepted[1].user
    session_id = draft_tests.owner_session(accepted).pk
    monkeypatch.setenv("EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.setattr(service, "_reference", lambda: object())
    start = Barrier(2)
    provider_entered = Event()
    release_provider = Event()
    observations_lock = Lock()
    backend_ids = set()
    external_calls = []

    class Adapter:
        def create(self, credential, **kwargs):
            assert not connection.in_atomic_block
            assert kwargs["payload"] == operation.payload
            with observations_lock:
                external_calls.append(operation.pk)
            provider_entered.set()
            if not release_provider.wait(timeout=15):
                raise AssertionError("Test did not release the synthetic provider.")
            if ambiguous:
                raise TimeoutError("Synthetic ambiguous provider outcome")
            return EventbriteDraftReceipt(
                "CONFIRMED", "456", "draft", operation.request_digest, "d" * 64
            )

    def dispatch():
        # Django connections are thread-local: each contender owns a real PG session.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_id = cursor.fetchone()[0]
            with observations_lock:
                backend_ids.add(backend_id)
            start.wait(timeout=10)
            try:
                result = service.execute_draft(
                    user=user,
                    administrator_session_id=session_id,
                    operation_id=operation.pk,
                    store=draft_tests.Store(),
                    adapter=Adapter(),
                )
            except PermissionDenied:
                return "refused"
            return result.status
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch), pool.submit(dispatch)]
        try:
            assert provider_entered.wait(timeout=10), "No contender reached the fake provider."
            # The observer is a third connection. Visibility while the provider is
            # blocked proves the durable claim committed before the external call.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                observer_id = cursor.fetchone()[0]
            claimed = DraftExecution.objects.get(pk=operation.pk)
            assert claimed.status == "executing"
            assert claimed.claimed_at is not None
            done, pending = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
            assert len(done) == 1 and len(pending) == 1
            assert next(iter(done)).result() == "refused"
            assert len(backend_ids) == 2
            assert observer_id not in backend_ids
            assert external_calls == [operation.pk]
        finally:
            release_provider.set()
        outcomes = sorted(future.result(timeout=10) for future in futures)

    expected_status = "unknown" if ambiguous else "succeeded"
    assert outcomes == sorted(["refused", expected_status])
    operation.refresh_from_db()
    assert operation.status == expected_status
    with pytest.raises(PermissionDenied):
        service.execute_draft(
            user=user,
            administrator_session_id=session_id,
            operation_id=operation.pk,
            store=draft_tests.Store(),
            adapter=Adapter(),
        )
    assert external_calls == [operation.pk]
