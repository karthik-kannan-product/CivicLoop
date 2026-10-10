"""Public event facts and content readiness, without synthetic audience policies."""

from datetime import UTC, date, datetime, time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from identity.models import AdministratorSession

from .engine import _draft

FACT_FIELDS = (
    "title",
    "date",
    "start_time",
    "end_time",
    "timezone",
    "city",
    "region",
    "country",
    "venue_name",
    "venue_address",
    "access_instructions",
    "signup_url",
)
SCHEMA_ID = "owner_event_draft_v1"


def validate_facts(value):
    if type(value) is not dict or not set(value) <= set(FACT_FIELDS):
        raise ValueError("invalid_event_facts")
    facts = {}
    for key, raw in value.items():
        if type(raw) is not str or len(raw) > (
            2000
            if key == "access_instructions"
            else 240
            if key == "title"
            else 64
            if key == "timezone"
            else 500
        ):
            raise ValueError("invalid_event_facts")
        facts[key] = raw.strip()
    if facts.get("date"):
        if date.fromisoformat(facts["date"]).isoformat() != facts["date"]:
            raise ValueError("invalid_event_facts")
    if facts.get("timezone"):
        try:
            ZoneInfo(facts["timezone"])
        except ZoneInfoNotFoundError:
            raise ValueError("invalid_event_facts") from None
    for key in ("start_time", "end_time"):
        if facts.get(key):
            if time.fromisoformat(facts[key]).strftime("%H:%M") != facts[key]:
                raise ValueError("invalid_event_facts")
    if facts.get("signup_url"):
        url = urlsplit(facts["signup_url"])
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise ValueError("invalid_event_facts")
    if all(facts.get(key) for key in ("date", "timezone", "start_time", "end_time")):
        points = []
        zone = ZoneInfo(facts["timezone"])
        for key in ("start_time", "end_time"):
            local = datetime.fromisoformat(facts["date"] + "T" + facts[key])
            first, second = local.replace(tzinfo=zone, fold=0), local.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                raise ValueError("invalid_event_facts")
            utc = first.astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) != local:
                raise ValueError("invalid_event_facts")
            points.append(utc)
        if points[1] <= points[0]:
            raise ValueError("invalid_event_facts")
    return facts


def source_kind(revision):
    if revision.snapshot.get("synthetic") is True:
        return "synthetic"
    if revision.source_snapshot_id:
        return "eventbrite"
    return "manual" if revision.snapshot.get("owner_event") is True else "unsupported"


def owner_session(actor, session_id):
    from django.utils import timezone

    now = timezone.now()
    return (
        AdministratorSession.objects.filter(
            pk=session_id,
            profile__user_id=actor.user_id,
            profile__status="active",
            profile__user__is_active=True,
            recovery_restricted=False,
            revoked_at__isnull=True,
            expires_at__gt=now,
            absolute_expires_at__gt=now,
            mfa_verified_at__isnull=False,
        ).first()
        if session_id is not None
        else None
    )


def prepare_owner_package(snapshot):
    facts = {}
    invalid = []
    for key in FACT_FIELDS:
        try:
            facts.update(validate_facts({key: snapshot.get(key, "")}))
        except ValueError:
            facts[key] = ""
            invalid.append(key)
    try:
        validate_facts(facts)
    except ValueError:
        invalid.extend(("start_time", "end_time"))
    missing = [key for key in FACT_FIELDS if not facts.get(key) or key in invalid]
    status = "needs_input" if missing else "ready_for_review"
    return {
        "schema_id": SCHEMA_ID,
        "status": status,
        "missing_fields": missing,
        "questions": [
            {"field": key, "prompt": f"Confirm {key.replace('_', ' ')}."} for key in missing
        ],
        "assets": {
            "invitation": _draft(facts, "Invitation"),
            "reminder": _draft(facts, "Reminder"),
            "social": {"body": f"{facts['title']} on {facts['date']}. {facts['signup_url']}"},
        },
        "audience": {
            "id": None,
            "name": "Choose in provider review",
            "member_count": 0,
            "language": "Unspecified",
        },
        "sponsor": {
            "passed": False,
            "tier": "",
            "expected_discount_percent": None,
            "actual_discount_percent": None,
            "general_ticket_price": None,
            "sponsor_ticket_price": None,
        },
        "lanes": {
            "event_readiness": {
                "label": "Event Readiness",
                "status": "needs_input" if missing else "complete",
                "summary": "Confirm public event facts."
                if missing
                else "Public event facts confirmed.",
            },
            "campaign_composer": {
                "label": "Content preparation",
                "status": "needs_input" if missing else "complete",
                "summary": "Event copy is for review only.",
            },
            "audience_policy": {
                "label": "Provider review",
                "status": "needs_input",
                "summary": "Choose exact audience and suppression in provider request review.",
            },
        },
        "evidence": [
            "Prepared only public event facts from the current revision.",
            "No audience segmentation or sponsor policy was applied.",
            "Provider operations require a separate exact owner request approval.",
        ],
    }
