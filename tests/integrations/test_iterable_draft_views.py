import json
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from integrations import iterable_views as views
from integrations.models import DraftExecution, TemplateExecution
from jsonschema import Draft202012Validator, FormatChecker

from tests.integrations.test_draft_operations import accepted as accepted
from tests.integrations.test_draft_operations import iterable_accepted as iterable_accepted
from tests.integrations.test_draft_operations import template_execute
from tests.integrations.test_draft_views import request

pytestmark = pytest.mark.django_db(transaction=True)
FIELDS = {
    "sender": {
        "fromEmail": "sender@example.test",
        "fromName": "Owner sender",
        "replyToEmail": "reply@example.test",
        "messageTypeId": 7,
    },
    "region": "us",
}
CAMPAIGN = {"name": "Hermes invitation", "listIds": [9], "suppressionListIds": [10]}


def validate_contract(payload, name):
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/integrations/iterable-owner-draft.schema.json"
        ).read_text()
    )
    schema["$ref"] = f"#/$defs/{name}"
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def prepare(accepted, value):
    return views.prepare(value, accepted[3].pk, accepted[4].pk)


def campaign(accepted, value):
    return views.prepare_campaign(value, accepted[3].pk, accepted[4].pk)


def test_owner_full_mfa_bound_actor_and_no_store(iterable_accepted):
    value = request(iterable_accepted)
    result = prepare(iterable_accepted, value)
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    data = json.loads(result.content)
    validate_contract(data, "Preparation")
    assert data["subject"] == iterable_accepted[4].proposal.content["invitation"]["subject"]
    assert data["template_execution"] is None and data["campaign_execution"] is None
    value.user = AnonymousUser()
    assert prepare(iterable_accepted, value).status_code == 401
    value = request(iterable_accepted)
    value.user = iterable_accepted[2].user
    assert prepare(iterable_accepted, value).status_code == 403
    value = request(iterable_accepted)
    value.administrator_session.recovery_restricted = True
    value.administrator_session.save()
    assert prepare(iterable_accepted, value).status_code == 403


def test_no_shared_sandbox_or_query_credentials(iterable_accepted):
    value = request(iterable_accepted)
    del value.administrator_session
    assert prepare(iterable_accepted, value).status_code == 403
    value = request(iterable_accepted)
    value.GET = {"token": "excluded"}
    assert prepare(iterable_accepted, value).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {**FIELDS, "subject": "override"},
        {**FIELDS, "region": "elsewhere"},
        {**FIELDS, "sender": {**FIELDS["sender"], "messageTypeId": True}},
        {**FIELDS, "sender": {**FIELDS["sender"], "fromName": "x" * 5000}},
    ],
)
def test_bounded_template_fields_do_not_accept_authored_content(iterable_accepted, body):
    assert prepare(iterable_accepted, request(iterable_accepted, "post", body)).status_code in (
        400,
        403,
    )
    assert not TemplateExecution.objects.exists()


def test_template_exact_review_and_no_campaign_before_confirmed_template(iterable_accepted):
    response = prepare(iterable_accepted, request(iterable_accepted, "post", FIELDS))
    assert response.status_code == 201
    data = json.loads(response.content)
    validate_contract(FIELDS, "TemplateRequest")
    validate_contract(data, "TemplateExecution")
    assert data["step"] == "template" and data["payload"]["fromName"] == "Owner sender"
    assert (
        campaign(iterable_accepted, request(iterable_accepted, "post", CAMPAIGN)).status_code == 403
    )
    assert not DraftExecution.objects.exists()
    bad = {"review_digest": "0" * 64, "revision_id": data["revision_id"]}
    assert (
        views.template_approve(
            request(iterable_accepted, "post", bad), data["operation_id"]
        ).status_code
        == 403
    )
    good = {**bad, "review_digest": data["review_digest"]}
    assert (
        views.template_approve(
            request(iterable_accepted, "post", good), data["operation_id"]
        ).status_code
        == 200
    )
    assert TemplateExecution.objects.get(pk=data["operation_id"]).receipt is None


def test_server_pins_template_and_false_schedule_no_intent_mutation(iterable_accepted, monkeypatch):
    template = template_execute(iterable_accepted, monkeypatch)
    response = campaign(iterable_accepted, request(iterable_accepted, "post", CAMPAIGN))
    assert response.status_code == 201
    data = json.loads(response.content)
    validate_contract(CAMPAIGN, "CampaignRequest")
    validate_contract(data, "CampaignExecution")
    assert data["payload"] == {**CAMPAIGN, "templateId": 44, "scheduleSend": False}
    assert data["provider_configuration"] == {
        "region": "us",
        "expected_template_digest": template.receipt["readback_digest"],
    }
    assert data["step"] == "campaign" and data["status"] == "pending" and data["receipt"] is None
    for extra in ({"scheduleSend": True}, {"templateId": 99}, {"region": "eu"}):
        assert (
            campaign(
                iterable_accepted, request(iterable_accepted, "post", {**CAMPAIGN, **extra})
            ).status_code
            == 400
        )
    approval = {"review_digest": data["review_digest"], "revision_id": data["revision_id"]}
    assert (
        views.campaign_approve(
            request(iterable_accepted, "post", approval), data["operation_id"]
        ).status_code
        == 200
    )
    iterable_accepted[4].refresh_from_db()
    assert iterable_accepted[4].status == "pending" and iterable_accepted[4].receipt is None
    value = request(iterable_accepted)
    value.user = iterable_accepted[2].user
    assert views.campaign_detail(value, data["operation_id"]).status_code == 403


def test_unknown_template_is_visible_without_claiming_campaign(iterable_accepted, monkeypatch):
    template_execute(iterable_accepted, monkeypatch, ambiguous=True)
    result = json.loads(prepare(iterable_accepted, request(iterable_accepted)).content)
    validate_contract(result, "Preparation")
    assert result["template_execution"]["status"] == "unknown"
    assert result["campaign_execution"] is None
    assert (
        campaign(iterable_accepted, request(iterable_accepted, "post", CAMPAIGN)).status_code == 403
    )


def test_csrf_and_safe_methods(iterable_accepted):
    middleware = CsrfViewMiddleware(lambda request: None)
    for view in [
        views.prepare,
        views.prepare_campaign,
        views.template_approve,
        views.template_execute,
        views.template_reconcile,
        views.campaign_approve,
        views.campaign_execute,
        views.campaign_reconcile,
    ]:
        assert (
            middleware.process_view(
                request(iterable_accepted, "post", FIELDS), view, (), {}
            ).status_code
            == 403
        )
    value = request(iterable_accepted)
    value.method = "DELETE"
    response = prepare(iterable_accepted, value)
    assert response.status_code == 405 and response.headers["Cache-Control"] == "no-store"


def test_duplicate_json_and_oversize_stream_rejected(iterable_accepted):
    from django.test import RequestFactory

    from tests.integrations.test_draft_operations import owner_session

    for raw in ('{"region":"us","region":"eu","sender":{}}', " " * 4097):
        value = RequestFactory().post(
            "/api/v1/iterable-review", data=raw, content_type="application/json"
        )
        value.user = iterable_accepted[1].user
        value.administrator_session = owner_session(iterable_accepted)
        value.META["CONTENT_LENGTH"] = "1"
        assert prepare(iterable_accepted, value).status_code == 400


def test_actual_campaign_api_receipt_and_unknown_at_most_once(iterable_accepted, monkeypatch):
    from integrations import draft_operations as service
    from integrations.iterable_drafts import IterableDraftReceipt

    from tests.integrations.test_draft_operations import IterableStore

    template_execute(iterable_accepted, monkeypatch)
    response = campaign(iterable_accepted, request(iterable_accepted, "post", CAMPAIGN))
    data = json.loads(response.content)
    operation_id = data["operation_id"]
    approval = {"review_digest": data["review_digest"], "revision_id": data["revision_id"]}
    result = views.campaign_approve(request(iterable_accepted, "post", approval), operation_id)
    assert result.status_code == 200
    calls = []

    class Adapter:
        def create(self, credential, **kwargs):
            calls.append("POST")
            assert kwargs["payload"]["scheduleSend"] is False
            assert kwargs["payload"]["templateId"] == 44
            return IterableDraftReceipt("UNKNOWN", "55", None, data["request_digest"])

        def reconcile(self, credential, **kwargs):
            calls.append("GET")
            assert kwargs["campaign_id"] == "55"
            return IterableDraftReceipt(
                "CONFIRMED", "55", "Ready", data["request_digest"], "f" * 64
            )

    monkeypatch.setattr(service, "PostgresSecretStore", IterableStore)
    monkeypatch.setattr(service.iterable_drafts, "IterableDraftAdapter", Adapter)
    result = views.campaign_execute(request(iterable_accepted, "post"), operation_id)
    assert result.status_code == 200 and json.loads(result.content)["status"] == "unknown"
    assert (
        views.campaign_execute(request(iterable_accepted, "post"), operation_id).status_code == 403
    )
    result = views.campaign_reconcile(request(iterable_accepted, "post"), operation_id)
    payload = json.loads(result.content)
    validate_contract(payload, "CampaignExecution")
    assert payload["status"] == "succeeded" and payload["receipt"]["provider_status"] == "Ready"
    assert calls == ["POST", "GET"]
