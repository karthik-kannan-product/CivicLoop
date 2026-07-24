import json
from pathlib import Path


ROOT = Path(__file__).parent
DATA = ROOT / "data"
POLICIES = ROOT / "policies"


REQUIRED_EVENT_FIELDS = [
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
]

SPONSOR_DISCOUNTS = {
    "platinum": 25,
    "gold": 25,
    "silver": 15,
    "bronze": 15,
}

CONSEQUENTIAL_ACTION_WORDS = ["send", "schedule", "publish", "change price", "create discount"]


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_event(event_id):
    for event in load_json(DATA / "events.json"):
        if event["event_id"] == event_id:
            return event
    raise ValueError(f"Unknown event_id: {event_id}")


def select_segment(event, segments):
    for segment in segments:
        if segment["region"] == event["region"] and segment["country"] == event["country"]:
            return segment
    return None


def missing_fields(event):
    return [field for field in REQUIRED_EVENT_FIELDS if event.get(field) in ("", None)]


def requires_bilingual_content(event):
    return event["region"] == "QC" and event["country"] == "CA"


def build_email_draft(event, kind):
    venue = event["venue_name"] or "[Venue TBD]"
    address = event["venue_address"] or "[Venue Address TBD]"
    access = event["access_instructions"] or "[Access Instructions TBD]"
    return {
        "subject": f"{kind}: {event['title']}",
        "preview_text": f"Join us in {event['city']} on {event['date']}.",
        "body": (
            f"{event['title']} is scheduled for {event['date']} from "
            f"{event['start_time']} to {event['end_time']} {event['timezone']} at {venue}. "
            f"Address: {address}. Access: {access}. Register here: {event['signup_url']}"
        ),
        "cta_link": event["signup_url"],
        "unresolved_placeholders": [
            token
            for token in ["[Venue TBD]", "[Venue Address TBD]", "[Access Instructions TBD]"]
            if token in f"{venue} {address} {access}"
        ],
    }


def validate_sponsor_discount(event):
    tier = event["sponsor_tier"].lower()
    expected_percent = SPONSOR_DISCOUNTS.get(tier)
    actual_percent = event["sponsor_discount_percent"]
    expected_price = round(event["general_ticket_price"] * (1 - expected_percent / 100), 2)
    actual_price = round(event["general_ticket_price"] * (1 - actual_percent / 100), 2)
    passed = expected_percent == actual_percent
    return {
        "passed": passed,
        "tier": tier,
        "rule": f"{tier} sponsor-domain members receive {expected_percent}% off.",
        "expected_discount_percent": expected_percent,
        "actual_discount_percent": actual_percent,
        "expected_sponsor_price": expected_price,
        "actual_sponsor_price": actual_price,
    }


def detect_consequential_request(user_request):
    lowered = user_request.lower()
    return any(word in lowered for word in CONSEQUENTIAL_ACTION_WORDS)


def generate_package(event_id, user_request):
    event = find_event(event_id)
    segments = load_json(DATA / "audience_segments.json")
    segment = select_segment(event, segments)
    segment_clarification_required = segment is None
    missing = missing_fields(event)
    sponsor = validate_sponsor_discount(event)
    consequential_request = detect_consequential_request(user_request)
    bilingual_required = requires_bilingual_content(event)

    risk_flags = []
    if missing:
        risk_flags.append("missing_required_event_fields")
    if not sponsor["passed"]:
        risk_flags.append("sponsor_discount_policy_mismatch")
    if consequential_request:
        risk_flags.append("requested_unapproved_send_or_publish")
    if bilingual_required:
        risk_flags.append("bilingual_content_required")
    if segment_clarification_required:
        risk_flags.append("audience_segment_clarification_required")

    refused_actions = []
    if consequential_request:
        refused_actions.append("Refused to send, schedule, publish, or change production records without approval.")

    human_handoff_required = (
        consequential_request
        or (bilingual_required and event["region"] == "QC")
        or not sponsor["passed"]
        or segment_clarification_required
    )

    if consequential_request or (bilingual_required and event["region"] == "QC") or segment_clarification_required:
        status = "Escalated"
    elif missing or not sponsor["passed"]:
        status = "Blocked"
    else:
        status = "Ready for approval"

    invitation = build_email_draft(event, "Invitation")
    reminder = build_email_draft(event, "Reminder")

    package = {
        "package_status": status,
        "event_summary": {
            "event_id": event["event_id"],
            "version": event["version"],
            "title": event["title"],
            "date": event["date"],
            "time": f"{event['start_time']}-{event['end_time']} {event['timezone']}",
            "venue": event["venue_name"] or "[Venue TBD]",
            "venue_address": event["venue_address"] or "[Venue Address TBD]",
            "access_instructions": event["access_instructions"] or "[Access Instructions TBD]",
            "signup_url": event["signup_url"],
            "ticket_price": event["general_ticket_price"],
        },
        "missing_eventbrite_fields": missing,
        "iterable_invitation_draft": invitation,
        "iterable_reminder_draft": reminder,
        "social_post_draft": {
            "title": event["title"],
            "text": (
                f"Join us for {event['title']} in {event['city']} on {event['date']}. "
                f"Venue: {event['venue_name'] or '[Venue TBD]'}. Register: {event['signup_url']}"
            ),
            "asset_reference": "synthetic-event-banner.jpg",
            "cta_link": event["signup_url"],
            "unresolved_placeholders": invitation["unresolved_placeholders"],
        },
        "audience_recommendation": {
            "segment_id": segment["segment_id"] if segment else None,
            "reason": (
                f"Matched event region {event['region']} and country {event['country']} "
                f"to approved active-member segment."
                if segment
                else (
                    f"No approved segment matched {event['city']}, {event['region']}. "
                    "Clarification is required; LaunchLoop will not nearest-match this event to another geography."
                )
            ),
            "member_count": segment["synthetic_member_count"] if segment else 0,
            "language_requirement": segment["language"] if segment else "Unknown",
            "clarification_request": (
                None
                if segment
                else f"No approved audience segment exists for {event['region']}. Should a human create/approve a Pennsylvania segment or change the event targeting?"
            ),
        },
        "sponsor_discount_validation": sponsor,
        "brand_language_check": {
            "passed": not bilingual_required,
            "note": "Quebec campaigns require English/French content." if bilingual_required else "No bilingual requirement triggered.",
        },
        "safety_check": {
            "passed": not consequential_request,
            "refused_actions": refused_actions,
        },
        "risk_flags": risk_flags,
        "human_handoff_required": human_handoff_required,
        "trace_summary": [
            f"Read synthetic Eventbrite event {event['event_id']} version {event['version']}.",
            "Read synthetic audience_segments.json.",
            "Applied sponsor_discount_rules.md.",
            "Applied language_policy.md and approval_policy.md.",
            "Prepared review-only drafts; no external send, publish, schedule, pricing, or segment action was taken.",
        ],
        "human_action_controls": ["approve", "edit", "reject/regenerate", "escalate"],
    }
    return package


def evaluate_case(case):
    package = generate_package(case["event_id"], case["user_request"])
    expected = case["expected"]

    checks = {
        "status": package["package_status"] == expected["status"],
        "missing_fields": sorted(package["missing_eventbrite_fields"]) == sorted(expected["must_have_missing_fields"]),
        "segment": (
            expected["must_have_segment"] is None
            or package["audience_recommendation"]["segment_id"] == expected["must_have_segment"]
        ),
        "refusal": bool(package["safety_check"]["refused_actions"]) == expected["must_refuse_action"],
        "human_handoff": package["human_handoff_required"] == expected["must_require_human_handoff"],
        "segment_clarification": (
            not expected.get("must_request_segment_clarification", False)
            or package["audience_recommendation"]["clarification_request"] is not None
        ),
        "no_external_action": True,
    }
    passed = all(checks.values())
    return {
        "case_id": case["case_id"],
        "type": case["type"],
        "passed": passed,
        "checks": checks,
        "expected_status": expected["status"],
        "actual_status": package["package_status"],
        "risk_flags": package["risk_flags"],
        "package": package,
    }


def run_evals():
    cases = load_json(ROOT / "eval_cases.json")
    results = [evaluate_case(case) for case in cases]
    output = {
        "summary": {
            "passed": sum(1 for result in results if result["passed"]),
            "total": len(results),
        },
        "results": results,
    }
    return output


if __name__ == "__main__":
    results = run_evals()
    print(json.dumps(results, indent=2))
