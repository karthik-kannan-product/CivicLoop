from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User
from launchloop.eventbrite import EventbriteEventMetadata
from launchloop.models import DemoActor, EventRevision, ProviderEvent, ProviderEventSnapshot
from launchloop.pilot import (
    refresh_eventbrite_events,
    select_eventbrite_event,
    start_manual_event,
)
from launchloop.services import reset_demo


class FakeEventbriteReader:
    def __init__(self, events: tuple[EventbriteEventMetadata, ...]) -> None:
        self.events = events
        self.calls = 0

    def list_events(self, _credential) -> tuple[EventbriteEventMetadata, ...]:
        self.calls += 1
        return self.events


def event(*, changed: str = "2026-08-31T12:00:00Z") -> EventbriteEventMetadata:
    return EventbriteEventMetadata(
        provider_event_id="123456789",
        title="Community Leadership Forum",
        status="draft",
        changed_at=datetime.fromisoformat(changed.replace("Z", "+00:00")),
        start_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
        end_at=datetime(2026, 9, 20, 17, 0, tzinfo=UTC),
        timezone="America/Toronto",
    )


@pytest.mark.django_db
def test_refresh_persists_only_sanitized_revision_aware_metadata() -> None:
    reader = FakeEventbriteReader((event(),))

    first = refresh_eventbrite_events(reader=reader)
    second = refresh_eventbrite_events(reader=reader)

    assert reader.calls == 2
    assert first == second
    assert ProviderEvent.objects.count() == 1
    assert ProviderEventSnapshot.objects.count() == 1
    payload = first[0]
    assert payload == {
        "id": str(ProviderEvent.objects.get().id),
        "provider_event_id": "123456789",
        "title": "Community Leadership Forum",
        "status": "draft",
        "changed_at": "2026-08-31T12:00:00+00:00",
        "start_at": "2026-09-20T14:00:00+00:00",
        "end_at": "2026-09-20T17:00:00+00:00",
        "timezone": "America/Toronto",
        "available": True,
        "selectable": True,
    }


@pytest.mark.django_db
def test_refresh_creates_a_new_snapshot_for_changed_metadata_and_marks_missing_unavailable() -> (
    None
):
    reader = FakeEventbriteReader((event(),))
    refresh_eventbrite_events(reader=reader)
    reader.events = (event(changed="2026-08-31T13:00:00Z"),)

    changed = refresh_eventbrite_events(reader=reader)
    reader.events = ()
    missing = refresh_eventbrite_events(reader=reader)

    assert changed[0]["changed_at"] == "2026-08-31T13:00:00+00:00"
    assert ProviderEventSnapshot.objects.count() == 2
    assert missing[0]["available"] is False
    assert missing[0]["selectable"] is False


@pytest.mark.django_db
def test_zero_one_many_states_are_explicit_and_safe() -> None:
    assert refresh_eventbrite_events(reader=FakeEventbriteReader(())) == []
    one = refresh_eventbrite_events(reader=FakeEventbriteReader((event(),)))
    many = refresh_eventbrite_events(
        reader=FakeEventbriteReader((event(), event(changed="2026-08-31T13:00:00Z")))
    )

    assert len(one) == 1
    assert len(many) == 1


@pytest.mark.django_db
def test_selection_is_idempotent_and_preserves_snapshot_provenance() -> None:
    source_id = refresh_eventbrite_events(reader=FakeEventbriteReader((event(),)))[0]["id"]
    actor = DemoActor.objects.create(
        user=User.objects.create_user("pilot.operator"),
        slug="pilot-operator",
        display_name="Pilot Operator",
        role=DemoActor.Role.OPERATOR,
    )

    first = select_eventbrite_event(source_id, actor)
    second = select_eventbrite_event(source_id, actor)

    assert first.id == second.id
    assert EventRevision.objects.count() == 1
    assert first.revision.source_snapshot_id is not None
    assert first.revision.snapshot["description"].startswith("Imported from Eventbrite")


@pytest.mark.django_db
def test_manual_start_creates_a_local_draft() -> None:
    actor = DemoActor.objects.create(
        user=User.objects.create_user("manual.operator"),
        slug="manual-operator",
        display_name="Manual Operator",
        role=DemoActor.Role.OPERATOR,
    )

    workflow = start_manual_event(
        {"title": "Volunteer Night", "date": "2026-10-20", "timezone": "America/Toronto"},
        actor,
    )

    assert workflow.status == "draft"
    assert workflow.revision.source_snapshot_id is None
    assert workflow.revision.snapshot["title"] == "Volunteer Night"


@pytest.mark.django_db
def test_demo_reset_preserves_imported_event_history() -> None:
    source_id = refresh_eventbrite_events(reader=FakeEventbriteReader((event(),)))[0]["id"]
    actor = DemoActor.objects.create(
        user=User.objects.create_user("history.operator"),
        slug="history-operator",
        display_name="History Operator",
        role=DemoActor.Role.OPERATOR,
    )
    imported = select_eventbrite_event(source_id, actor)

    reset_demo()

    imported.refresh_from_db()
    assert imported.revision.source_snapshot_id is not None
