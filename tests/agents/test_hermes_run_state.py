import uuid

import pytest
from agents.models import AgentRunControl, AgentRunEvent, HermesRunBinding
from django.db import IntegrityError, transaction
from django.utils import timezone

from tests.agents.test_runs import create_run


@pytest.mark.django_db
def test_run_events_are_append_only_and_sequence_unique() -> None:
    run = create_run()
    first = AgentRunEvent.objects.create(
        run=run,
        sequence=1,
        event_type="queued",
        outcome="accepted",
        detail_digest="a" * 64,
    )
    with pytest.raises(ValueError, match="append-only"):
        first.save(update_fields=["outcome"])
    with pytest.raises(IntegrityError), transaction.atomic():
        AgentRunEvent.objects.create(
            run=run,
            sequence=1,
            event_type="running",
            outcome="started",
            detail_digest="b" * 64,
        )
    second = AgentRunEvent.objects.create(
        run=run,
        sequence=2,
        event_type="running",
        outcome="started",
        detail_digest="b" * 64,
    )
    assert list(run.events.order_by("sequence").values_list("pk", flat=True)) == [
        first.pk,
        second.pk,
    ]


@pytest.mark.django_db
def test_binding_is_immutable_and_digests_canonical_revision() -> None:
    run = create_run()
    actor = run.event_revision.author
    correlation_id = uuid.uuid4()
    binding = HermesRunBinding.objects.create(
        run=run,
        actor=actor,
        correlation_id=correlation_id,
        revision_digest="0" * 64,
    )
    assert binding.revision_digest == (
        "a8198524f58e72b56283ab71ebddada22840f108b46ef7165bb3fca9919c5558"
    )
    binding.refresh_from_db()
    assert binding.correlation_id == correlation_id
    with pytest.raises(ValueError, match="immutable"):
        binding.save(update_fields=["revision_digest"])


@pytest.mark.django_db
def test_binding_correlation_id_is_unique_and_control_can_change() -> None:
    run = create_run()
    binding = HermesRunBinding.objects.create(run=run, actor=run.event_revision.author)
    other = create_run()
    with pytest.raises(IntegrityError), transaction.atomic():
        HermesRunBinding.objects.create(
            run=other,
            actor=other.event_revision.author,
            correlation_id=binding.correlation_id,
        )

    control = AgentRunControl.objects.create(run=run)
    assert control.cancel_requested_at is None
    assert control.admission_disabled is False
    control.cancel_requested_at = timezone.now()
    control.admission_disabled = True
    control.save()
    control.refresh_from_db()
    assert control.cancel_requested_at is not None
    assert control.admission_disabled is True
