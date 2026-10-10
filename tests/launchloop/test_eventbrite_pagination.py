from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.utils import timezone
from integrations.types import SecretLease, SecretReference
from launchloop.eventbrite import EventbriteReadError, PaginatedEventbriteReader


def lease():
    return SecretLease(
        SecretReference(uuid4(), "eventbrite", "metadata", 1),
        uuid4(),
        None,
        "test",
        timezone.now() + timedelta(seconds=30),
        bytearray(b"synthetic-token"),
    )


def raw_event(event_id):
    return {
        "id": str(event_id),
        "name": {"text": f"Draft {event_id}"},
        "status": "draft",
        "changed": "2026-10-09T12:00:00Z",
        "created": "2025-01-01T00:00:00Z",
        "start": {"utc": "2026-12-01T12:00:00Z", "timezone": "UTC"},
        "description": {"text": "not retained"},
    }


class Reader(PaginatedEventbriteReader):
    def __init__(self, organizations, event_pages):
        self.organizations = organizations
        self.event_pages = event_pages
        self.urls = []

    def _get_json(self, url, token):
        self.urls.append(url)
        query = parse_qs(urlsplit(url).query)
        if "/users/" in url:
            page = int(query["page"][0])
            organizations, more = self.organizations[page]
            return {
                "organizations": [{"id": str(value)} for value in organizations],
                "pagination": {"has_more_items": more},
            }
        organization = urlsplit(url).path.split("/")[3]
        assert query["status"] == ["draft"]
        assert int(query["page_size"][0]) <= 20
        values, more = self.event_pages[organization, int(query["page"][0])]
        return {"events": values, "pagination": {"has_more_items": more}}


def test_product_walks_event_and_organization_pages_without_smoke_cap():
    reader = Reader(
        {1: ([42], True), 2: ([43], False)},
        {
            ("42", 1): ([raw_event(1), raw_event(2)], True),
            ("42", 2): ([raw_event(3)], False),
            ("43", 1): ([raw_event(4)], False),
        },
    )
    cursor = None
    ids = []
    for _ in range(4):
        reader.urls = []
        page = reader.list_page(lease(), account="account:1", cursor=cursor, page_size=2)
        assert len(reader.urls) <= 4
        ids += [event.provider_event_id for event in page.events]
        cursor = page.next_cursor
        if page.complete:
            break
    assert ids == ["1", "2", "3", "4"]
    assert page.complete
    assert not hasattr(page.events[0], "description")


@pytest.mark.parametrize("change", ["tamper", "account", "query"])
def test_cursor_is_opaque_and_bound_to_account_and_query(change):
    reader = Reader({1: ([42], False)}, {("42", 1): ([raw_event(1)], True)})
    page = reader.list_page(lease(), account="account:1", page_size=1)
    cursor = page.next_cursor
    kwargs = {"account": "account:1", "page_size": 1}
    if change == "tamper":
        cursor = cursor[:20] + ("A" if cursor[20] != "A" else "B") + cursor[21:]
    if change == "account":
        kwargs["account"] = "account:2"
    if change == "query":
        kwargs["changed_since"] = "2026-01-01T00:00:00Z"
    reader.urls = []
    with pytest.raises(EventbriteReadError):
        reader.list_page(lease(), cursor=cursor, **kwargs)
    assert reader.urls == []


@pytest.mark.parametrize("size", [0, 21, True])
def test_invalid_page_sizes_rejected_without_provider_request(size):
    reader = Reader({}, {})
    with pytest.raises(EventbriteReadError):
        reader.list_page(lease(), account="account", page_size=size)
    assert reader.urls == []


def test_empty_organizations_yield_bounded_partial_page_and_resumable_cursor():
    reader = Reader(
        {1: ([1, 2, 3, 4, 5], False)}, {(str(org), 1): ([], False) for org in range(1, 6)}
    )
    page = reader.list_page(lease(), account="account")
    assert page.events == () and not page.complete
    assert len(reader.urls) == 4
    page = reader.list_page(lease(), account="account", cursor=page.next_cursor)
    assert page.complete


def test_dates_filter_created_and_changed_without_losing_cursor_progress():
    reader = Reader(
        {1: ([42], False)}, {("42", 1): ([raw_event(1)], True), ("42", 2): ([raw_event(2)], False)}
    )
    page = reader.list_page(lease(), account="account", created_since="2026-01-01T00:00:00Z")
    assert page.events == () and page.complete
    page = reader.list_page(
        lease(),
        account="account",
        created_since="2025-01-01T00:00:00Z",
        changed_until="2026-12-31T00:00:00Z",
    )
    assert page.events[0].provider_event_id == "1"
    assert page.next_cursor


def test_duplicate_ids_are_deduplicated_and_oversized_response_rejected():
    reader = Reader({1: ([42], False)}, {("42", 1): ([raw_event(1), raw_event(1)], False)})
    assert len(reader.list_page(lease(), account="account", page_size=2).events) == 1
    with pytest.raises(EventbriteReadError):
        reader.list_page(lease(), account="account", page_size=1)


def test_provider_errors_are_preserved():
    reader = Reader({}, {})

    def fail(url, token):
        raise EventbriteReadError("rate_limit")

    reader._get_json = fail
    with pytest.raises(EventbriteReadError, match="rate_limit"):
        reader.list_page(lease(), account="account")


def test_total_page_deadline_fails_before_more_provider_requests(monkeypatch):
    reader = Reader({1: ([42], False)}, {("42", 1): ([raw_event(1)], False)})
    values = iter([0, 0, 26])
    monkeypatch.setattr("launchloop.eventbrite.time.monotonic", lambda: next(values))
    with pytest.raises(EventbriteReadError, match="timeout"):
        reader.list_page(lease(), account="account")
    assert len(reader.urls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://www.eventbriteapi.com/v3/users/me/organizations/",
        "https://example.com/v3/users/me/organizations/",
        "https://www.eventbriteapi.com/v3/events/1/attendees/",
    ],
)
def test_product_reuses_transport_host_and_endpoint_allowlist(url):
    with pytest.raises(EventbriteReadError, match="network"):
        PaginatedEventbriteReader()._get_json(url, "synthetic-token")
