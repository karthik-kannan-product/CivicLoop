from launchloop.engine import prepare_package


def test_incomplete_event_is_blocked_without_inventing_venue_details() -> None:
    result = prepare_package(
        {
            "title": "New York International Youth Day Networking Breakfast",
            "city": "New York",
            "region": "NY",
            "country": "US",
            "date": "2026-08-12",
            "start_time": "09:00",
            "end_time": "12:00",
            "timezone": "America/New_York",
            "venue_name": "",
            "venue_address": "",
            "access_instructions": "",
            "general_ticket_price": 40,
            "signup_url": "https://example.test/eventbrite/ny-youth-day",
            "sponsor_tier": "gold",
            "sponsor_discount_percent": 25,
        }
    )

    assert result["status"] == "needs_input"
    assert result["missing_fields"] == [
        "venue_name",
        "venue_address",
        "access_instructions",
    ]
    assert "[Venue TBD]" in result["assets"]["invitation"]["body"]
    assert result["lanes"]["event_readiness"]["status"] == "needs_input"


def test_complete_new_york_event_produces_an_approval_ready_package() -> None:
    result = prepare_package(
        {
            "title": "New York International Youth Day Networking Breakfast",
            "city": "New York",
            "region": "NY",
            "country": "US",
            "date": "2026-08-12",
            "start_time": "09:00",
            "end_time": "12:00",
            "timezone": "America/New_York",
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street, New York, NY 10001",
            "access_instructions": "Use the 10th Avenue entrance.",
            "general_ticket_price": 40,
            "signup_url": "https://example.test/eventbrite/ny-youth-day",
            "sponsor_tier": "gold",
            "sponsor_discount_percent": 25,
        }
    )

    assert result["status"] == "ready_for_review"
    assert result["audience"]["name"] == "New York Active Members"
    assert result["audience"]["member_count"] == 418
    assert result["sponsor"]["expected_discount_percent"] == 25
    assert all(lane["status"] == "complete" for lane in result["lanes"].values())


def test_draft_does_not_duplicate_terminal_punctuation() -> None:
    result = prepare_package(
        {
            "title": "Community Breakfast",
            "city": "New York",
            "region": "NY",
            "country": "US",
            "date": "2026-08-12",
            "start_time": "09:00",
            "end_time": "12:00",
            "timezone": "America/New_York",
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street",
            "access_instructions": "Use the 10th Avenue entrance.",
            "general_ticket_price": 40,
            "signup_url": "https://example.test/event",
            "sponsor_tier": "gold",
            "sponsor_discount_percent": 25,
        }
    )

    assert "entrance.. Register" not in result["assets"]["invitation"]["body"]
    assert "entrance. Register" in result["assets"]["invitation"]["body"]
