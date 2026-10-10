import copy
import io
import json
from datetime import timedelta
from http.client import BadStatusLine, IncompleteRead
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest
from django.utils import timezone
from integrations.eventbrite_drafts import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    EventbriteDraftAdapter,
    EventbriteDraftError,
    _NoRedirects,
    compute_request_digest,
    readback_digest,
    validate_payload,
)
from integrations.exceptions import SecretUnavailable
from integrations.types import SecretLease, SecretReference

TOKEN = b"synthetic-eventbrite-token"
CREATE = {
    "event": {
        "name": {"html": "Community workshop"},
        "start": {"utc": "2027-01-10T15:00:00Z", "timezone": "UTC"},
        "end": {"utc": "2027-01-10T17:00:00Z", "timezone": "UTC"},
        "currency": "USD",
        "summary": "A community workshop",
        "online_event": True,
    }
}


def lease(**overrides):
    values = dict(
        reference=SecretReference(UUID(int=1), "eventbrite", "draft_write", 1),
        caller_id=UUID(int=2),
        workflow_id=UUID(int=3),
        purpose="eventbrite_draft_write",
        expires_at=timezone.now() + timedelta(seconds=60),
        _plaintext=bytearray(TOKEN),
    )
    values.update(overrides)
    return SecretLease(**values)


def draft(**overrides):
    result = {
        "id": "456",
        "organization_id": "123",
        "status": "draft",
        "published": None,
        "changed": "2026-10-09T12:00:00Z",
        **validate_payload(CREATE, create=True)["event"],
    }
    result.update(overrides)
    return result


class Response:
    status = 200

    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, count):
        assert count == MAX_RESPONSE_BYTES + 1
        return self.body[:count]


def wire(monkeypatch, responses):
    calls = []
    queue = list(responses)

    class Opener:
        def open(self, request, timeout):
            calls.append((request.method, request.full_url, request.data, dict(request.headers)))
            assert timeout == TIMEOUT_SECONDS
            assert request.get_header("Authorization") == "Bearer " + TOKEN.decode()
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            return Response(response)

    monkeypatch.setattr("integrations.eventbrite_drafts.build_opener", lambda *args: Opener())
    return calls


def test_create_posts_once_then_reads_draft_and_closes_lease(monkeypatch):
    calls = wire(monkeypatch, [draft(), draft()])
    credential = lease()
    plaintext = credential._plaintext
    receipt = EventbriteDraftAdapter().create(credential, organization_id="123", payload=CREATE)
    assert receipt.outcome == "CONFIRMED"
    assert receipt.provider_id == "456" and receipt.provider_status == "draft"
    assert receipt.request_digest == compute_request_digest("create", "123", None, CREATE)
    assert receipt.readback_digest == readback_digest(draft())
    assert [(method, url) for method, url, *_ in calls] == [
        ("POST", "https://www.eventbriteapi.com/v3/organizations/123/events/"),
        ("GET", "https://www.eventbriteapi.com/v3/events/456/"),
    ]
    body = json.loads(calls[0][2])
    assert body["event"]["listed"] is False and body["event"]["shareable"] is False
    assert all("Idempotency-Key" not in call[3] for call in calls)
    assert credential._plaintext is None and plaintext == bytearray(len(TOKEN))
    with pytest.raises(SecretUnavailable):
        credential.use(lambda view: None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("publish", True),
        ("status", "live"),
        ("ticket_classes", []),
        ("capacity", 100),
        ("price", 10),
        ("discount", "PROMO"),
        ("description", {"html": "deprecated"}),
        ("listed", True),
        ("shareable", True),
        ("is_series", True),
    ],
)
def test_forbidden_fields_rejected_before_secret_or_network(monkeypatch, field, value):
    calls = wire(monkeypatch, [])
    payload = copy.deepcopy(CREATE)
    payload["event"][field] = value
    credential = lease()
    with pytest.raises(EventbriteDraftError, match="invalid_request"):
        EventbriteDraftAdapter().create(credential, organization_id="123", payload=payload)
    assert calls == [] and credential._plaintext == TOKEN


@pytest.mark.parametrize("organization_id", ["123/../456", "123?publish=true", "0", "１２３", 123])
def test_path_identifiers_cannot_escape_allowlist(monkeypatch, organization_id):
    calls = wire(monkeypatch, [])
    with pytest.raises(EventbriteDraftError, match="invalid_request"):
        EventbriteDraftAdapter().create(lease(), organization_id=organization_id, payload=CREATE)
    assert calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": {"html": ""}},
        {"start": {"utc": "bad", "timezone": "UTC"}},
        {"start": {"utc": "2027-01-11T15:00:00Z", "timezone": "UTC"}},
        {"start": {"utc": "2027-01-10T15:00:00Z", "timezone": "missing-zone"}},
        {"currency": "usd"},
        {"online_event": "true"},
        {"venue_id": "123"},
        {"summary": "x" * 141},
    ],
)
def test_invalid_field_shapes_fail_locally(mutation):
    payload = copy.deepcopy(CREATE)
    payload["event"].update(mutation)
    with pytest.raises(EventbriteDraftError):
        validate_payload(payload, create=True)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(TOKEN.decode()),
        URLError(TOKEN.decode()),
        HTTPError("https://provider", 500, TOKEN.decode(), {}, io.BytesIO(TOKEN)),
    ],
)
def test_ambiguous_post_never_retries_and_has_no_secret_error(monkeypatch, failure):
    calls = wire(monkeypatch, [failure])
    receipt = EventbriteDraftAdapter().create(lease(), organization_id="123", payload=CREATE)
    assert receipt.outcome == "UNKNOWN" and receipt.provider_id is None
    assert len(calls) == 1 and calls[0][0] == "POST"
    assert TOKEN.decode() not in repr(receipt)


@pytest.mark.parametrize(
    "failure",
    [
        IncompleteRead(TOKEN, 99),
        BadStatusLine(TOKEN.decode()),
    ],
    ids=["incomplete-read", "bad-status-line"],
)
def test_http_protocol_error_after_post_is_unknown_without_repeat(monkeypatch, failure):
    calls = wire(monkeypatch, [failure])
    credential = lease()
    plaintext = credential._plaintext
    receipt = EventbriteDraftAdapter().create(credential, organization_id="123", payload=CREATE)
    assert receipt.outcome == "UNKNOWN" and receipt.provider_id is None
    assert receipt.error_category == "network"
    assert len(calls) == 1 and calls[0][0] == "POST"
    assert TOKEN.decode() not in repr(receipt)
    assert credential._plaintext is None and plaintext == bytearray(len(TOKEN))


@pytest.mark.parametrize(
    "failure",
    [
        IncompleteRead(TOKEN, 99),
        BadStatusLine(TOKEN.decode()),
    ],
    ids=["incomplete-read", "bad-status-line"],
)
def test_http_protocol_error_during_readback_preserves_known_id(monkeypatch, failure):
    calls = wire(monkeypatch, [draft(), failure])
    credential = lease()
    plaintext = credential._plaintext
    receipt = EventbriteDraftAdapter().create(credential, organization_id="123", payload=CREATE)
    assert receipt.outcome == "UNKNOWN" and receipt.provider_id == "456"
    assert receipt.error_category == "network"
    assert [call[0] for call in calls] == ["POST", "GET"]
    assert TOKEN.decode() not in repr(receipt)
    assert credential._plaintext is None and plaintext == bytearray(len(TOKEN))


@pytest.mark.parametrize(
    "body",
    [b"not-json", b"[]", b"x" * (MAX_RESPONSE_BYTES + 1), {}],
    ids=["invalid-json", "array", "oversize", "missing-id"],
)
def test_malformed_post_is_unknown_without_repeat(monkeypatch, body):
    calls = wire(monkeypatch, [body])
    receipt = EventbriteDraftAdapter().create(lease(), organization_id="123", payload=CREATE)
    assert receipt.outcome == "UNKNOWN" and len(calls) == 1


@pytest.mark.parametrize(
    "code,category",
    [
        (400, "provider_validation"),
        (401, "authentication"),
        (403, "forbidden"),
        (429, "rate_limited"),
        (302, "redirect"),
    ],
)
def test_definitive_http_failure_safe_category_no_body(monkeypatch, code, category):
    failure = HTTPError("https://provider", code, TOKEN.decode(), {}, io.BytesIO(TOKEN))
    calls = wire(monkeypatch, [failure])
    with pytest.raises(EventbriteDraftError) as raised:
        EventbriteDraftAdapter().create(lease(), organization_id="123", payload=CREATE)
    assert raised.value.category == category and str(raised.value) == category
    assert len(calls) == 1


@pytest.mark.parametrize(
    "after",
    [
        draft(status="live"),
        draft(published="2026-10-09T12:00:00Z"),
        draft(organization_id="999"),
        draft(id="789"),
        draft(summary="old summary"),
        TimeoutError(),
    ],
)
def test_readback_must_prove_identity_draft_and_payload(monkeypatch, after):
    calls = wire(monkeypatch, [draft(), after])
    receipt = EventbriteDraftAdapter().create(lease(), organization_id="123", payload=CREATE)
    assert receipt.outcome == "UNKNOWN" and receipt.provider_id == "456"
    assert receipt.provider_status is None and receipt.readback_digest is None
    assert len(calls) == 2


def test_update_reads_before_and_after(monkeypatch):
    before, after = draft(), draft(summary="Edited summary", changed="2026-10-09T12:01:00Z")
    calls = wire(monkeypatch, [before, after, after])
    receipt = EventbriteDraftAdapter().update(
        lease(),
        organization_id="123",
        event_id="456",
        payload={"event": {"summary": "Edited summary"}},
        expected_readback_digest=readback_digest(before),
    )
    assert receipt.outcome == "CONFIRMED" and receipt.readback_digest == readback_digest(after)
    assert [call[0] for call in calls] == ["GET", "POST", "GET"]


@pytest.mark.parametrize(
    "before,category",
    [
        (draft(status="live"), "unsafe_provider_state"),
        (draft(published="2020-01-01T00:00:00Z"), "unsafe_provider_state"),
        (draft(is_series=True), "unsafe_provider_state"),
        (draft(series_id="888"), "unsafe_provider_state"),
        (draft(organization_id="999"), "identity_mismatch"),
        (draft(changed="new-revision"), "stale_revision"),
    ],
)
def test_update_refuses_live_series_wrong_org_and_stale_revision(monkeypatch, before, category):
    calls = wire(monkeypatch, [before])
    with pytest.raises(EventbriteDraftError, match=category):
        EventbriteDraftAdapter().update(
            lease(),
            organization_id="123",
            event_id="456",
            payload={"event": {"summary": "Edited"}},
            expected_readback_digest=readback_digest(draft()),
        )
    assert [call[0] for call in calls] == ["GET"]


def test_update_currency_is_an_economic_mutation(monkeypatch):
    calls = wire(monkeypatch, [])
    with pytest.raises(EventbriteDraftError, match="invalid_request"):
        EventbriteDraftAdapter().update(
            lease(),
            organization_id="123",
            event_id="456",
            payload={"event": {"currency": "EUR"}},
            expected_readback_digest=readback_digest(draft()),
        )
    assert calls == []


def test_reconcile_known_id_is_read_only(monkeypatch):
    calls = wire(monkeypatch, [draft()])
    receipt = EventbriteDraftAdapter().reconcile(
        lease(),
        organization_id="123",
        event_id="456",
        payload=CREATE,
        request_digest=compute_request_digest("create", "123", None, CREATE),
    )
    assert receipt.outcome == "CONFIRMED" and [call[0] for call in calls] == ["GET"]


def test_reconcile_unknown_id_requires_human_without_network(monkeypatch):
    calls = wire(monkeypatch, [])
    credential = lease()
    receipt = EventbriteDraftAdapter().reconcile(
        credential,
        organization_id="123",
        event_id=None,
        payload=CREATE,
        request_digest=compute_request_digest("create", "123", None, CREATE),
    )
    assert (
        receipt.outcome == "UNKNOWN" and receipt.error_category == "human_reconciliation_required"
    )
    assert calls == [] and credential._plaintext == TOKEN


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "metadata_read"},
        {"workflow_id": None},
        {"reference": SecretReference(UUID(int=1), "iterable", "draft_write", 1)},
    ],
)
def test_write_requires_provider_purpose_and_workflow(monkeypatch, overrides):
    calls = wire(monkeypatch, [])
    credential = lease(**overrides)
    with pytest.raises(EventbriteDraftError, match="invalid_credential"):
        EventbriteDraftAdapter().create(credential, organization_id="123", payload=CREATE)
    assert calls == [] and credential._plaintext == TOKEN


def test_redirect_handler_never_follows_redirects():
    assert (
        _NoRedirects().redirect_request(None, None, 302, "redirect", {}, "https://evil.test")
        is None
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/events/456/publish/"),
        ("POST", "/events/456/ticket_classes/"),
        ("GET", "/events/456/?expand=attendees"),
        ("DELETE", "/events/456/"),
        ("POST", "https://evil.test/"),
    ],
)
def test_transport_endpoint_allowlist(monkeypatch, method, path):
    calls = wire(monkeypatch, [])
    with pytest.raises(EventbriteDraftError, match="invalid_request"):
        EventbriteDraftAdapter()._request(method, path, TOKEN.decode())
    assert calls == []


@pytest.mark.parametrize("field", ["name", "start", "end", "currency"])
def test_create_requires_complete_minimum(field):
    payload = copy.deepcopy(CREATE)
    del payload["event"][field]
    with pytest.raises(EventbriteDraftError, match="invalid_request"):
        validate_payload(payload, create=True)


def test_missing_publication_field_cannot_prove_unpublished(monkeypatch):
    after = draft()
    del after["published"]
    wire(monkeypatch, [draft(), after])
    receipt = EventbriteDraftAdapter().create(lease(), organization_id="123", payload=CREATE)
    assert receipt.outcome == "UNKNOWN" and receipt.error_category == "unsafe_provider_state"


def test_update_timeout_retains_known_id_and_closes_lease(monkeypatch):
    calls = wire(monkeypatch, [draft(), TimeoutError(TOKEN.decode())])
    credential = lease()
    plaintext = credential._plaintext
    receipt = EventbriteDraftAdapter().update(
        credential,
        organization_id="123",
        event_id="456",
        payload={"event": {"summary": "Edited"}},
        expected_readback_digest=readback_digest(draft()),
    )
    assert receipt.outcome == "UNKNOWN" and receipt.provider_id == "456"
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert credential._plaintext is None and plaintext == bytearray(len(TOKEN))


def test_reconciliation_digest_tampering_fails_before_network(monkeypatch):
    calls = wire(monkeypatch, [])
    credential = lease()
    with pytest.raises(EventbriteDraftError, match="invalid_request"):
        EventbriteDraftAdapter().reconcile(
            credential,
            organization_id="123",
            event_id="456",
            payload=CREATE,
            request_digest="0" * 64,
        )
    assert calls == [] and credential._plaintext == TOKEN


def test_update_reconciliation_only_reads_persisted_id(monkeypatch):
    payload = {"event": {"summary": "Edited"}}
    calls = wire(monkeypatch, [draft(summary="Edited")])
    receipt = EventbriteDraftAdapter().reconcile(
        lease(),
        organization_id="123",
        event_id="456",
        payload=payload,
        action="update",
        request_digest=compute_request_digest("update", "123", "456", payload),
    )
    assert receipt.outcome == "CONFIRMED" and [call[0] for call in calls] == ["GET"]


def test_expired_lease_never_connects(monkeypatch):
    calls = wire(monkeypatch, [])
    credential = lease(expires_at=timezone.now() - timedelta(seconds=1))
    plaintext = credential._plaintext
    with pytest.raises(SecretUnavailable):
        EventbriteDraftAdapter().create(credential, organization_id="123", payload=CREATE)
    assert calls == [] and credential._plaintext is None and plaintext == bytearray(len(TOKEN))
