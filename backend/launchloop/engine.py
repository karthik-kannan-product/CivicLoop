from typing import Any

REQUIRED_FIELDS = (
    "title",
    "city",
    "date",
    "start_time",
    "end_time",
    "timezone",
    "venue_name",
    "venue_address",
    "access_instructions",
    "general_ticket_price",
    "signup_url",
)

AUDIENCES = {
    ("NY", "US"): {
        "id": "new_york_active_members",
        "name": "New York Active Members",
        "member_count": 418,
        "language": "English",
    }
}

SPONSOR_DISCOUNTS = {
    "platinum": 25,
    "gold": 25,
    "silver": 15,
    "bronze": 15,
}


def _draft(event: dict[str, Any], kind: str) -> dict[str, str]:
    venue = event.get("venue_name") or "[Venue TBD]"
    address = event.get("venue_address") or "[Venue Address TBD]"
    access = event.get("access_instructions") or "[Access Instructions TBD]"
    access_sentence = access if str(access).endswith((".", "!", "?")) else f"{access}."
    return {
        "subject": f"{kind}: {event['title']}",
        "body": (
            f"{event['title']} is scheduled for {event['date']} from "
            f"{event['start_time']} to {event['end_time']} {event['timezone']} at {venue}. "
            f"Address: {address}. Access: {access_sentence} Register: {event['signup_url']}"
        ),
    }


def prepare_package(event: dict[str, Any]) -> dict[str, Any]:
    """Create a grounded, review-only campaign package from one event revision."""
    missing = [field for field in REQUIRED_FIELDS if event.get(field) in ("", None)]
    audience = AUDIENCES.get((event.get("region"), event.get("country")))
    sponsor_tier = str(event.get("sponsor_tier", "")).lower()
    expected_discount = SPONSOR_DISCOUNTS.get(sponsor_tier)
    actual_discount = event.get("sponsor_discount_percent")
    sponsor_passed = expected_discount is not None and expected_discount == actual_discount

    questions = [
        {
            "field": field,
            "prompt": {
                "venue_name": "What is the confirmed venue name?",
                "venue_address": "What is the complete venue address?",
                "access_instructions": (
                    "What arrival or accessibility instructions should guests receive?"
                ),
            }.get(field, f"What is the confirmed {field.replace('_', ' ')}?"),
        }
        for field in missing
    ]
    status = "ready_for_review"
    if missing:
        status = "needs_input"
    elif audience is None or not sponsor_passed:
        status = "blocked"

    lane_status = "complete" if status == "ready_for_review" else "needs_input"
    if status == "blocked":
        lane_status = "blocked"

    invitation = _draft(event, "Invitation")
    reminder = _draft(event, "Reminder")
    return {
        "status": status,
        "missing_fields": missing,
        "questions": questions,
        "assets": {
            "invitation": invitation,
            "reminder": reminder,
            "social": {
                "body": (
                    f"Join us for {event['title']} in {event['city']} on {event['date']}. "
                    f"Venue: {event.get('venue_name') or '[Venue TBD]'}. "
                    f"Register: {event['signup_url']}"
                )
            },
        },
        "audience": audience
        or {
            "id": None,
            "name": "Clarification required",
            "member_count": 0,
            "language": "Unknown",
        },
        "sponsor": {
            "passed": sponsor_passed,
            "tier": sponsor_tier,
            "expected_discount_percent": expected_discount,
            "actual_discount_percent": actual_discount,
            "general_ticket_price": event.get("general_ticket_price"),
            "sponsor_ticket_price": (
                round(event["general_ticket_price"] * (1 - expected_discount / 100), 2)
                if expected_discount is not None
                else None
            ),
        },
        "lanes": {
            "event_readiness": {
                "label": "Event Readiness",
                "status": "complete" if not missing else "needs_input",
                "summary": (
                    "All required event facts are grounded."
                    if not missing
                    else f"{len(missing)} event facts need an operator response."
                ),
            },
            "campaign_composer": {
                "label": "Campaign Composer",
                "status": lane_status,
                "summary": "Prepared invitation, reminder, and social drafts.",
            },
            "audience_policy": {
                "label": "Audience and Policy",
                "status": "complete" if audience and sponsor_passed else "blocked",
                "summary": (
                    "Matched the approved New York audience and validated sponsor pricing."
                    if audience and sponsor_passed
                    else "Audience or sponsor policy needs human review."
                ),
            },
        },
        "evidence": [
            "Read one immutable event revision.",
            "Matched only an approved geography-specific audience.",
            "Recomputed sponsor discount math deterministically.",
            "Prepared review-only drafts; no external action was taken.",
        ],
    }
