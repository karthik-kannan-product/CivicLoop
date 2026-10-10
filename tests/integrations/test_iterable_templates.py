import copy
from types import SimpleNamespace
from uuid import UUID

import pytest
from integrations.iterable_drafts import IterableDraftError, template_content_digest
from integrations.iterable_templates import (
    IterableTemplateAdapter,
    author_payload,
    compute_request_digest,
    validate_template_payload,
)

from tests.integrations.test_iterable_drafts import lease


def payload():
    return author_payload(
        SimpleNamespace(
            pk=UUID(int=12),
            operation_kind="create_iterable_email_draft",
            proposal=SimpleNamespace(
                content={"invitation": {"subject": "Join us", "body": "A <script> & B\nWelcome"}}
            ),
        ),
        {
            "fromEmail": "sender@example.test",
            "fromName": "Sender",
            "replyToEmail": "reply@example.test",
            "messageTypeId": 7,
        },
    )


class Provider(IterableTemplateAdapter):
    def __init__(self, *, collision=False, ambiguous=False, mutated=False):
        self.entries = [{"templateId": 44}] if collision else []
        self.calls = []
        self.content = None
        self.ambiguous = ambiguous
        self.mutated = mutated

    def _request(self, method, path, token, *, region, payload=None):
        self.calls.append((method, path))
        if method == "POST":
            assert self.entries == []
            self.content = copy.deepcopy(payload)
            self.entries = [{"templateId": 44}]
            if self.ambiguous:
                raise IterableDraftError("timeout")
            return {"code": "Success"}
        if "getByClientTemplateId" in path:
            return {"templates": copy.deepcopy(self.entries)}
        result = {"templateId": 44, **self.content}
        if self.mutated:
            result["subject"] = "changed"
        return result


def test_content_is_authored_and_escaped_then_confirmed():
    value = payload()
    assert value["html"] == "<p>A &lt;script&gt; &amp; B<br>\nWelcome</p>"
    provider = Provider()
    receipt = provider.create(lease(), payload=value, region="eu")
    assert receipt.outcome == "CONFIRMED"
    assert receipt.provider_id == "44"
    assert receipt.readback_digest == template_content_digest(value)
    assert [m for m, _ in provider.calls] == ["GET", "POST", "GET", "GET"]


def test_existing_client_id_never_upserts():
    provider = Provider(collision=True)
    with pytest.raises(IterableDraftError, match="unowned_collision"):
        provider.create(lease(), payload=payload())
    assert [m for m, _ in provider.calls] == ["GET"]


def test_ambiguous_write_reconciles_with_get_only():
    provider = Provider(ambiguous=True)
    value = payload()
    first = provider.create(lease(), payload=value)
    assert first.outcome == "UNKNOWN" and first.provider_id is None
    second = provider.reconcile(lease(), payload=value, request_digest=first.request_digest)
    assert second.outcome == "CONFIRMED"
    assert sum(m == "POST" for m, _ in provider.calls) == 1
    assert [m for m, _ in provider.calls[-2:]] == ["GET", "GET"]


def test_changed_readback_cannot_confirm():
    assert Provider(mutated=True).create(lease(), payload=payload()).outcome == "UNKNOWN"


@pytest.mark.parametrize(
    "key,value",
    [
        ("clientTemplateId", 123),
        ("clientTemplateId", "foreign"),
        ("html", "<script>bad</script>"),
        ("fromEmail", "a@example.test\nBcc:x"),
        ("subject", "{{user.email}}"),
        ("plainText", "[[feed]]"),
        ("fromName", "Name\r\n"),
    ],
)
def test_unreviewed_fields_denied(key, value):
    request = payload()
    request[key] = value
    with pytest.raises(IterableDraftError):
        validate_template_payload(request)


@pytest.mark.parametrize(
    "path", ["/api/templates/email/proof", "/api/templates/email/update", "/api/campaigns/create"]
)
def test_transport_has_no_send_or_update_path(path):
    with pytest.raises(IterableDraftError, match="invalid_request"):
        IterableTemplateAdapter()._request(
            "POST", path, "synthetic", region="us", payload=payload()
        )


def test_digest_binds_region_sender_and_content():
    request = payload()
    assert compute_request_digest("eu", request) != compute_request_digest("us", request)
    changed = {**request, "fromName": "Changed"}
    assert compute_request_digest("us", changed) != compute_request_digest("us", request)


@pytest.mark.parametrize(
    "extra",
    [
        {"ccEmails": ["other@example.test"]},
        {"dataFeedIds": [4]},
        {"campaignDataFields": {"x": "y"}},
        {"mergeDataFeedContext": True},
    ],
)
def test_unreviewed_readback_effects_denied(extra):
    class ExtraProvider(Provider):
        def _request(self, method, path, *args, **kwargs):
            result = super()._request(method, path, *args, **kwargs)
            if path.startswith("/api/templates/email/get"):
                result.update(extra)
            return result

    assert ExtraProvider().create(lease(), payload=payload()).outcome == "UNKNOWN"


def test_transport_exact_payload_and_redaction(monkeypatch):
    import json

    from tests.integrations.test_iterable_drafts import TIMEOUT_SECONDS, TOKEN, Response

    value = payload()
    queue = [
        {"templates": []},
        {"code": "Success"},
        {"templates": [{"templateId": 44}]},
        {"templateId": 44, **value, "ccEmails": [], "bccEmails": []},
    ]
    calls = []

    class Opener:
        def open(self, request, timeout):
            assert timeout == TIMEOUT_SECONDS
            assert request.get_header("Api-key") == TOKEN.decode()
            calls.append(request)
            return Response(queue.pop(0))

    monkeypatch.setattr("integrations.iterable_templates.build_opener", lambda *args: Opener())
    receipt = IterableTemplateAdapter().create(lease(), payload=value, region="eu")
    assert receipt.outcome == "CONFIRMED"
    assert json.loads(calls[1].data) == value
    assert calls[1].full_url == "https://api.eu.iterable.com/api/templates/email/upsert"
    assert all(request.headers == {} and request.unredirected_hdrs == {} for request in calls)


@pytest.mark.parametrize("invalid", [True, 0, 2**31, "7"])
def test_message_type_id_matches_official_int32_schema(invalid):
    with pytest.raises(IterableDraftError):
        validate_template_payload({**payload(), "messageTypeId": invalid})
