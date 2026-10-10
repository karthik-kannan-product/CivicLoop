import copy
import io
import json
from datetime import timedelta
from http.client import IncompleteRead
from urllib.error import HTTPError
from uuid import UUID

import pytest
from django.utils import timezone
from integrations.iterable_drafts import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    IterableDraftAdapter,
    IterableDraftError,
    compute_request_digest,
    template_content_digest,
    validate_payload,
)
from integrations.types import SecretLease, SecretReference

TOKEN = b"synthetic-iterable-token"
PAYLOAD = {
    "name": "Workshop email",
    "templateId": 123,
    "listIds": [9],
    "suppressionListIds": [10],
    "scheduleSend": False,
}
TEMPLATE = {
    "templateId": 123,
    "subject": "Workshop",
    "html": "<p>Join us</p>",
    "fromEmail": "synthetic@example.test",
    "fromName": "Test",
}
TEMPLATE_DIGEST = template_content_digest(TEMPLATE)


def lease(**overrides):
    values = dict(
        reference=SecretReference(UUID(int=1), "iterable", "draft_write", 1),
        caller_id=UUID(int=2),
        workflow_id=UUID(int=3),
        purpose="iterable_draft_write",
        expires_at=timezone.now() + timedelta(seconds=60),
        _plaintext=bytearray(TOKEN),
    )
    values.update(overrides)
    return SecretLease(**values)


def campaign(**overrides):
    value = {
        "id": 456,
        "name": PAYLOAD["name"],
        "campaignState": "Ready",
        "messageMedium": "Email",
        "type": "Blast",
        "templateId": 789,
        "listIds": [9],
        "suppressionListIds": [10],
        "updatedAt": 123456,
    }
    value.update(overrides)
    return value


class Response:
    status = 200

    def __init__(self, value):
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, count):
        assert count == MAX_RESPONSE_BYTES + 1
        return self.body[:count]


def wire(monkeypatch, values):
    queue = list(values)
    calls = []

    class Opener:
        def open(self, request, timeout):
            assert timeout == TIMEOUT_SECONDS
            assert request.get_header("Api-key") == TOKEN.decode()
            calls.append((request.method, request.full_url, request.data, request))
            value = queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return Response(value)

    monkeypatch.setattr("integrations.iterable_drafts.build_opener", lambda *args: Opener())
    return calls


def create(adapter=None, credential=None, **kwargs):
    return (adapter or IterableDraftAdapter()).create(
        credential or lease(),
        payload=PAYLOAD,
        expected_template_digest=TEMPLATE_DIGEST,
        **kwargs,
    )


@pytest.mark.parametrize("state", ["Draft", "Ready"])
@pytest.mark.parametrize("region", ["us", "eu"])
def test_unscheduled_blast_validates_email_copy_and_consumes_lease(monkeypatch, state, region):
    calls = wire(
        monkeypatch,
        [
            TEMPLATE,
            {"campaignId": 456},
            campaign(campaignState=state),
            {**TEMPLATE, "templateId": 789},
        ],
    )
    credential = lease()
    plaintext = credential._plaintext
    receipt = create(credential=credential, region=region)
    assert receipt.outcome == "CONFIRMED"
    assert receipt.provider_id == "456"
    assert receipt.provider_status == state
    assert len(receipt.readback_digest) == 64
    assert receipt.request_digest == compute_request_digest(region, PAYLOAD, TEMPLATE_DIGEST)
    assert [method for method, *_ in calls] == ["GET", "POST", "GET", "GET"]
    host = "api.iterable.com" if region == "us" else "api.eu.iterable.com"
    assert [url for _, url, *_ in calls] == [
        f"https://{host}/api/templates/email/get?templateId=123",
        f"https://{host}/api/campaigns/create",
        f"https://{host}/api/campaigns/456",
        f"https://{host}/api/templates/email/get?templateId=789",
    ]
    assert json.loads(calls[1][2]) == PAYLOAD
    assert not any(calls[0][3].headers.values())
    assert credential._plaintext is None
    assert not any(plaintext)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scheduleSend", True),
        ("scheduleSend", 0),
        ("listIds", []),
        ("listIds", [True]),
        ("listIds", [9, 9]),
        ("templateId", "123"),
        ("suppressionListIds", None),
        ("name", ""),
        ("sendAt", "2027-01-01"),
        ("dataFields", {"recipient": "unsafe"}),
    ],
)
def test_invalid_or_sending_payload_cannot_reach_transport(monkeypatch, field, value):
    calls = wire(monkeypatch, [])
    payload = {**PAYLOAD, field: value}
    with pytest.raises(IterableDraftError, match="invalid_request"):
        IterableDraftAdapter().create(
            lease(), payload=payload, expected_template_digest=TEMPLATE_DIGEST
        )
    assert calls == []


def test_explicit_suppression_and_false_are_required():
    for field in ("suppressionListIds", "scheduleSend"):
        payload = copy.deepcopy(PAYLOAD)
        del payload[field]
        with pytest.raises(IterableDraftError):
            validate_payload(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"campaignState": "Scheduled"},
        {"campaignState": {}},
        {"campaignState": "Running"},
        {"startAt": 123},
        {"startAt": 0},
        {"type": "Triggered"},
        {"messageMedium": "SMS"},
        {"id": 999},
        {"id": True},
        {"listIds": [8]},
        {"suppressionListIds": []},
        {"name": "Another campaign"},
        {"workflowId": 1},
        {"recurringCampaignId": 1},
        {"endedAt": 1},
        {"templateId": None},
    ],
)
def test_unsafe_or_mismatched_readback_is_unknown_no_second_post(monkeypatch, overrides):
    calls = wire(monkeypatch, [TEMPLATE, {"campaignId": 456}, campaign(**overrides)])
    receipt = create()
    assert receipt.outcome == "UNKNOWN"
    assert receipt.provider_id == "456"
    assert len([call for call in calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize(
    "missing",
    [("listIds",), ("suppressionListIds",), ("listIds", "suppressionListIds")],
)
def test_missing_recipient_list_readback_is_unknown_and_cleans_lease(monkeypatch, missing):
    readback = campaign()
    for key in missing:
        del readback[key]
    calls = wire(monkeypatch, [TEMPLATE, {"campaignId": 456}, readback])
    credential = lease()
    plaintext = credential._plaintext

    receipt = create(credential=credential)

    assert receipt.outcome == "UNKNOWN"
    assert receipt.provider_id == "456"
    assert receipt.error_category == "readback_mismatch"
    assert [call[0] for call in calls] == ["GET", "POST", "GET"]
    assert credential._plaintext is None
    assert not any(plaintext)


@pytest.mark.parametrize(
    "missing",
    [("listIds",), ("suppressionListIds",), ("listIds", "suppressionListIds")],
)
def test_reconcile_missing_recipient_list_readback_is_unknown(monkeypatch, missing):
    readback = campaign()
    for key in missing:
        del readback[key]
    calls = wire(monkeypatch, [readback])
    credential = lease()
    plaintext = credential._plaintext

    receipt = IterableDraftAdapter().reconcile(
        credential,
        campaign_id="456",
        payload=PAYLOAD,
        expected_template_digest=TEMPLATE_DIGEST,
        request_digest=compute_request_digest("us", PAYLOAD, TEMPLATE_DIGEST),
    )

    assert receipt.outcome == "UNKNOWN"
    assert receipt.provider_id == "456"
    assert receipt.error_category == "readback_mismatch"
    assert [call[0] for call in calls] == ["GET"]
    assert credential._plaintext is None
    assert not any(plaintext)


def test_changed_source_is_rejected_before_write(monkeypatch):
    calls = wire(monkeypatch, [{**TEMPLATE, "html": "changed"}])
    with pytest.raises(IterableDraftError, match="stale_template"):
        create()
    assert len(calls) == 1
    assert calls[0][0] == "GET"


def test_changed_copy_is_unknown(monkeypatch):
    wire(
        monkeypatch,
        [
            TEMPLATE,
            {"campaignId": 456},
            campaign(),
            {**TEMPLATE, "templateId": 789, "subject": "changed"},
        ],
    )
    assert create().error_category == "readback_mismatch"


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("secret"), IncompleteRead(b"synthetic"), b"not json", {"campaignId": "456"}],
)
def test_ambiguous_post_is_unknown_and_never_retried(monkeypatch, failure):
    calls = wire(monkeypatch, [TEMPLATE, failure])
    receipt = create()
    assert receipt.outcome == "UNKNOWN"
    assert receipt.provider_id is None
    assert len(calls) == 2
    assert "secret" not in repr(receipt)


def test_known_id_reconciliation_only_reads_copy(monkeypatch):
    calls = wire(monkeypatch, [campaign(), {**TEMPLATE, "templateId": 789}])
    receipt = IterableDraftAdapter().reconcile(
        lease(),
        campaign_id="456",
        payload=PAYLOAD,
        expected_template_digest=TEMPLATE_DIGEST,
        request_digest=compute_request_digest("us", PAYLOAD, TEMPLATE_DIGEST),
    )
    assert receipt.outcome == "CONFIRMED"
    assert [call[0] for call in calls] == ["GET", "GET"]


def test_unknown_id_requires_human_reconciliation_without_credential_use(monkeypatch):
    calls = wire(monkeypatch, [])
    credential = lease()
    receipt = IterableDraftAdapter().reconcile(
        credential,
        campaign_id=None,
        payload=PAYLOAD,
        expected_template_digest=TEMPLATE_DIGEST,
        request_digest=compute_request_digest("us", PAYLOAD, TEMPLATE_DIGEST),
    )
    assert receipt.error_category == "human_reconciliation_required"
    assert calls == []
    assert credential._used is False


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/campaigns/456/send", "POST"),
        ("/api/campaigns/456/schedule", "POST"),
        ("/api/campaigns/activateTriggered", "POST"),
        ("/api/templates/email/proof", "POST"),
        ("/api/users/get", "GET"),
        ("/api/lists/subscribe", "POST"),
        ("/api/templates/email/get?templateId=123&locale=x", "GET"),
    ],
)
def test_transport_cannot_send_activate_proof_or_access_recipients(monkeypatch, path, method):
    calls = wire(monkeypatch, [])
    with pytest.raises(IterableDraftError, match="invalid_request"):
        IterableDraftAdapter()._request(method, path, TOKEN.decode(), region="us")
    assert calls == []


@pytest.mark.parametrize(
    "status,category",
    [(401, "authentication"), (429, "rate_limited"), (302, "redirect"), (500, "provider_error")],
)
def test_provider_error_is_redacted_and_headers_removed(monkeypatch, status, category):
    error = HTTPError("https://api.iterable.com", status, "secret body", {}, io.BytesIO(b"secret"))
    calls = wire(monkeypatch, [error])
    with pytest.raises(IterableDraftError) as exc:
        create()
    assert str(exc.value) == category
    assert calls[0][3].headers == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"workflow_id": None},
        {"purpose": "health_check"},
        {"reference": SecretReference(UUID(int=1), "eventbrite", "x", 1)},
    ],
)
def test_wrong_lease_cannot_reach_transport(monkeypatch, overrides):
    calls = wire(monkeypatch, [])
    with pytest.raises(IterableDraftError, match="invalid_credential"):
        create(credential=lease(**overrides))
    assert calls == []


@pytest.mark.parametrize(
    "body", [b"[1]", b"{", b"x" * (MAX_RESPONSE_BYTES + 1)], ids=["array", "malformed", "oversize"]
)
def test_invalid_or_oversize_response_is_redacted(monkeypatch, body):
    wire(monkeypatch, [body])
    with pytest.raises(IterableDraftError, match="invalid_response"):
        create()


def test_review_digest_binds_template_and_region():
    digest = compute_request_digest("us", PAYLOAD, TEMPLATE_DIGEST)
    assert digest != compute_request_digest("eu", PAYLOAD, TEMPLATE_DIGEST)
    assert digest != compute_request_digest("us", PAYLOAD, "0" * 64)


def test_reconcile_refuses_unapproved_request_without_transport(monkeypatch):
    calls = wire(monkeypatch, [])
    with pytest.raises(IterableDraftError, match="invalid_request"):
        IterableDraftAdapter().reconcile(
            lease(),
            campaign_id="456",
            payload=PAYLOAD,
            expected_template_digest=TEMPLATE_DIGEST,
            request_digest="0" * 64,
        )
    assert calls == []
