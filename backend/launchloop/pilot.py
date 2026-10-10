from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Max
from identity.models import AdministratorSession
from integrations.exceptions import SecretUnavailable
from integrations.models import ConnectionState, IntegrationConnection
from integrations.secret_store import EVENTBRITE_READ_PURPOSE, PostgresSecretStore
from integrations.types import SecretLease, SecretReference
from observability.runtime import get_runtime
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes

from .eventbrite import (
    EventbriteEventMetadata,
    EventbriteReader,
    EventbriteReadError,
    PaginatedEventbriteReader,
)
from .models import (
    AuditEvent,
    DemoActor,
    Event,
    EventRevision,
    ProviderEvent,
    ProviderEventSnapshot,
    Workflow,
    WorkflowTransition,
)

SELECTABLE_STATUSES = frozenset({"draft", "live"})


def _fingerprint(event: EventbriteEventMetadata) -> str:
    safe = {
        "provider_event_id": event.provider_event_id,
        "title": event.title,
        "status": event.status,
        "changed_at": event.changed_at.isoformat(),
        "start_at": event.start_at.isoformat() if event.start_at else None,
        "end_at": event.end_at.isoformat() if event.end_at else None,
        "timezone": event.timezone,
    }
    return hashlib.sha256(
        json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _payload(source: ProviderEvent) -> dict[str, Any]:
    snapshot = source.current_snapshot
    if snapshot is None:
        raise RuntimeError("Provider event has no current snapshot.")
    return {
        "id": str(source.id),
        "provider_event_id": source.provider_event_id,
        "title": snapshot.title,
        "status": snapshot.status,
        "changed_at": snapshot.provider_changed_at.isoformat(),
        "start_at": snapshot.start_at.isoformat() if snapshot.start_at else None,
        "end_at": snapshot.end_at.isoformat() if snapshot.end_at else None,
        "timezone": snapshot.timezone,
        "available": source.available,
        "selectable": source.available and snapshot.status in SELECTABLE_STATUSES,
    }


@transaction.atomic
def refresh_eventbrite_events(
    *,
    reader: EventbriteReader,
    credential: SecretLease | None = None,
    supplied_events: tuple[EventbriteEventMetadata, ...] | None = None,
    reconcile_missing: bool = True,
) -> list[dict[str, Any]]:
    events = supplied_events if supplied_events is not None else reader.list_events(credential)
    seen: set[str] = set()
    for event in events:
        seen.add(event.provider_event_id)
        source, _ = ProviderEvent.objects.select_for_update().get_or_create(
            provider="eventbrite", provider_event_id=event.provider_event_id
        )
        snapshot, _ = ProviderEventSnapshot.objects.get_or_create(
            source=source,
            fingerprint=_fingerprint(event),
            defaults={
                "title": event.title,
                "status": event.status,
                "provider_changed_at": event.changed_at,
                "start_at": event.start_at,
                "end_at": event.end_at,
                "timezone": event.timezone,
            },
        )
        source.current_snapshot = snapshot
        source.available = True
        source.save(update_fields=("current_snapshot", "available", "last_seen_at"))

    if reconcile_missing:
        ProviderEvent.objects.filter(provider="eventbrite").exclude(
            provider_event_id__in=seen
        ).update(available=False)
    sources = (
        ProviderEvent.objects.filter(provider="eventbrite")
        .select_related("current_snapshot")
        .order_by("current_snapshot__start_at", "provider_event_id")
    )
    return [_payload(source) for source in sources]


def list_eventbrite_events() -> list[dict[str, Any]]:
    sources = (
        ProviderEvent.objects.filter(provider="eventbrite", current_snapshot__status="draft")
        .select_related("current_snapshot")
        .order_by("-current_snapshot__provider_changed_at", "provider_event_id")[:20]
    )
    return [_payload(source) for source in sources]


def refresh_configured_eventbrite_events(
    *,
    administrator: AdministratorSession,
    reader: EventbriteReader | None = None,
    cursor: str | None = None,
    page_size: int = 20,
    statuses: tuple[str, ...] = ("draft",),
    changed_since: str | None = None,
    changed_until: str | None = None,
    created_since: str | None = None,
    created_until: str | None = None,
) -> dict[str, Any]:
    try:
        connection = IntegrationConnection.objects.select_related("secret").get(
            provider="eventbrite"
        )
    except IntegrationConnection.DoesNotExist:
        raise ValueError("eventbrite_not_configured") from None
    if connection.state == ConnectionState.DISABLED or connection.secret is None:
        raise ValueError("eventbrite_not_configured")
    secret = connection.secret
    reference = SecretReference(
        id=secret.id, provider=secret.provider, scope=secret.scope, version=secret.version
    )
    attributes = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.TOOL.value,
        "civicloop.provider": "eventbrite",
        "civicloop.operation": "metadata_read",
    }
    try:
        with PostgresSecretStore().lease(
            reference,
            caller_id=administrator.id,
            workflow_id=None,
            purpose=EVENTBRITE_READ_PURPOSE,
            ttl=timedelta(seconds=30),
        ) as lease:
            with get_runtime().start_span("eventbrite.metadata_read", attributes=attributes):
                product_reader = reader or PaginatedEventbriteReader()
                if isinstance(product_reader, PaginatedEventbriteReader):
                    page = product_reader.list_page(
                        lease,
                        account=f"{connection.id}:{secret.id}:{secret.version}",
                        cursor=cursor,
                        page_size=page_size,
                        statuses=statuses,
                        changed_since=changed_since,
                        changed_until=changed_until,
                        created_since=created_since,
                        created_until=created_until,
                    )
                    refresh_eventbrite_events(
                        reader=product_reader, supplied_events=page.events, reconcile_missing=False
                    )
                    ids = [event.provider_event_id for event in page.events]
                    sources = ProviderEvent.objects.filter(
                        provider="eventbrite", provider_event_id__in=ids
                    ).select_related("current_snapshot")
                    by_id = {source.provider_event_id: source for source in sources}
                    events = [
                        {
                            **_payload(by_id[event.provider_event_id]),
                            "source_fingerprint": by_id[
                                event.provider_event_id
                            ].current_snapshot.fingerprint,
                        }
                        for event in page.events
                    ]
                    result = {
                        "events": events,
                        "next_cursor": page.next_cursor,
                        "has_more": not page.complete,
                        "complete": page.complete,
                    }
                else:
                    events = refresh_eventbrite_events(reader=product_reader, credential=lease)
                    result = {
                        "events": events,
                        "next_cursor": None,
                        "has_more": False,
                        "complete": True,
                    }
    except (EventbriteReadError, SecretUnavailable) as error:
        category = error.category if isinstance(error, EventbriteReadError) else "unavailable"
        AuditEvent.objects.create(
            action="eventbrite_metadata_refresh_failed",
            target_type="integration",
            target_id="eventbrite",
            details={"failure_category": category},
        )
        raise ValueError(f"eventbrite_{category}") from None
    AuditEvent.objects.create(
        action="eventbrite_metadata_refreshed",
        target_type="integration",
        target_id="eventbrite",
        details={"event_count": len(events)},
    )
    return result


def owner_operator(administrator: AdministratorSession) -> DemoActor:
    user = administrator.profile.user
    actor, _ = DemoActor.objects.update_or_create(
        user=user,
        defaults={
            "slug": f"owner-{administrator.profile_id}",
            "display_name": user.get_full_name() or user.username,
            "role": DemoActor.Role.OPERATOR,
        },
    )
    return actor


@transaction.atomic
def select_eventbrite_event(source_id: uuid.UUID, actor: DemoActor) -> Workflow:
    try:
        source = ProviderEvent.objects.select_for_update().get(id=source_id)
    except ProviderEvent.DoesNotExist:
        raise ValueError("event_not_found") from None
    snapshot = source.current_snapshot
    if not source.available or snapshot is None or snapshot.status not in SELECTABLE_STATUSES:
        raise ValueError("event_unavailable")
    event = source.local_event
    if event is None:
        event = Event.objects.create(
            slug=f"eventbrite-{source.provider_event_id}", title=snapshot.title
        )
        source.local_event = event
        source.save(update_fields=("local_event", "last_seen_at"))
    elif event.title != snapshot.title:
        event.title = snapshot.title
        event.save(update_fields=("title",))
    workflow = Workflow.objects.filter(event=event).select_related("revision").first()
    if workflow is not None and workflow.revision.source_snapshot_id == snapshot.id:
        return workflow
    if workflow is not None and workflow.status not in {
        Workflow.Status.DRAFT,
        Workflow.Status.NEEDS_INPUT,
    }:
        raise ValueError("review_in_progress")
    version = (event.revisions.aggregate(value=Max("version"))["value"] or 0) + 1
    start, end = snapshot.start_at, snapshot.end_at
    facts = {
        "title": snapshot.title,
        "city": "",
        "region": "",
        "country": "",
        "date": start.date().isoformat() if start else "",
        "start_time": start.time().replace(tzinfo=None).isoformat(timespec="minutes")
        if start
        else "",
        "end_time": end.time().replace(tzinfo=None).isoformat(timespec="minutes") if end else "",
        "timezone": snapshot.timezone,
        "venue_name": "",
        "venue_address": "",
        "access_instructions": "",
        "description": "Imported from Eventbrite for local review. Provider text is not copied.",
        "general_ticket_price": 0,
        "signup_url": "",
        "sponsor_tier": "",
        "sponsor_discount_percent": 0,
    }
    revision = EventRevision.objects.create(
        event=event, version=version, snapshot=facts, author=actor, source_snapshot=snapshot
    )
    if workflow is None:
        workflow = Workflow.objects.create(event=event, revision=revision)
        previous = ""
    else:
        previous = workflow.status
        workflow.revision = revision
        workflow.status = Workflow.Status.DRAFT
        workflow.package = None
        workflow.package_hash = ""
        workflow.save(update_fields=("revision", "status", "package", "package_hash", "updated_at"))
    WorkflowTransition.objects.create(
        workflow=workflow,
        actor=actor,
        from_status=previous,
        to_status=Workflow.Status.DRAFT,
        action="eventbrite_event_selected",
        details={"source_snapshot_id": str(snapshot.id)},
    )
    return workflow


@transaction.atomic
def update_event_facts(workflow_id, body, actor):
    from agents.models import AgentRun

    from .owner_events import source_kind, validate_facts

    workflow = Workflow.objects.select_for_update().select_related("revision").get(pk=workflow_id)
    if source_kind(workflow.revision) not in ("manual", "eventbrite"):
        raise ValueError("owner_event_required")
    if workflow.revision.author_id != actor.pk or actor.role != DemoActor.Role.OPERATOR:
        raise ValueError("event_owner_required")
    if AgentRun.objects.filter(workflow=workflow, status__in=("queued", "running")).exists():
        raise ValueError("review_in_progress")
    if workflow.status not in (
        Workflow.Status.DRAFT,
        Workflow.Status.NEEDS_INPUT,
        Workflow.Status.READY_FOR_REVIEW,
    ):
        raise ValueError("review_in_progress")
    changes = validate_facts(body)
    if not changes:
        raise ValueError("invalid_event_facts")
    facts = dict(workflow.revision.snapshot)
    facts.update(changes)
    revision = EventRevision.objects.create(
        event=workflow.event,
        version=workflow.revision.version + 1,
        snapshot=facts,
        author=actor,
        source_snapshot=workflow.revision.source_snapshot,
    )
    previous = workflow.status
    workflow.revision = revision
    workflow.status = Workflow.Status.DRAFT
    workflow.package = None
    workflow.package_hash = ""
    workflow.save(update_fields=("revision", "status", "package", "package_hash", "updated_at"))
    workflow.event.title = facts["title"]
    workflow.event.save(update_fields=("title",))
    WorkflowTransition.objects.create(
        workflow=workflow,
        actor=actor,
        from_status=previous,
        to_status=Workflow.Status.DRAFT,
        action="owner_event_facts_saved",
        details={"revision": revision.version},
    )
    return workflow


@transaction.atomic
def start_manual_event(body: dict[str, Any], actor: DemoActor) -> Workflow:
    from .owner_events import FACT_FIELDS, validate_facts

    facts = dict.fromkeys(FACT_FIELDS, "")
    facts.update(validate_facts(body))
    if not all(facts[key] for key in ("title", "date", "timezone")):
        raise ValueError("invalid_event_brief")
    facts["owner_event"] = True
    event = Event.objects.create(slug=f"manual-{uuid.uuid4().hex[:20]}", title=facts["title"])
    revision = EventRevision.objects.create(event=event, version=1, snapshot=facts, author=actor)
    workflow = Workflow.objects.create(event=event, revision=revision)
    WorkflowTransition.objects.create(
        workflow=workflow,
        actor=actor,
        from_status="",
        to_status=Workflow.Status.DRAFT,
        action="manual_event_started",
        details={"revision": 1},
    )
    return workflow
