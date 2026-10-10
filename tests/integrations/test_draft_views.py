import json
import uuid
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory
from integrations import draft_views
from integrations.models import DraftExecution
from jsonschema import Draft202012Validator, FormatChecker

from tests.integrations.test_draft_operations import accepted as accepted
from tests.integrations.test_draft_operations import owner_session

pytestmark = pytest.mark.django_db(transaction=True)

FIELDS = {
    "title": "Owner reviewed event",
    "summary": "Owner reviewed summary",
    "organization_id": "123",
    "start_local": "2027-01-01T12:00",
    "end_local": "2027-01-01T13:00",
    "timezone": "UTC",
    "currency": "USD",
}


def validate_contract(payload, definition):
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/integrations/eventbrite-owner-draft.schema.json"
        ).read_text()
    )
    schema["$ref"] = f"#/$defs/{definition}"
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def request(accepted, method="get", body=None):
    value = getattr(RequestFactory(), method)(
        "/api/v1/draft-review",
        data=json.dumps(body or {}) if method == "post" else {},
        content_type="application/json",
    )
    value.user = accepted[1].user
    value.administrator_session = owner_session(accepted)
    return value


def preparation(accepted, value):
    return draft_views.prepare(value, accepted[3].pk, accepted[4].pk)


def test_prepare_requires_owner_full_session_and_bound_operator(accepted):
    value = request(accepted)
    assert preparation(accepted, value).status_code == 200
    validate_contract(json.loads(preparation(accepted, request(accepted)).content), "Preparation")
    assert json.loads(preparation(accepted, request(accepted)).content)["intent_id"] == str(
        accepted[4].pk
    )
    value.user = AnonymousUser()
    assert preparation(accepted, value).status_code == 401
    value = request(accepted)
    value.administrator_session.recovery_restricted = True
    value.administrator_session.save()
    assert preparation(accepted, value).status_code == 403


def test_other_user_and_shared_sandbox_cannot_prepare_or_read(accepted):
    value = request(accepted)
    value.user = accepted[2].user
    assert preparation(accepted, value).status_code == 403
    value = request(accepted)
    del value.administrator_session
    assert preparation(accepted, value).status_code == 403


def test_wrong_run_and_query_credentials_rejected(accepted):
    value = request(accepted)
    assert draft_views.prepare(value, uuid.uuid4(), accepted[4].pk).status_code == 404
    value.GET = {"token": "excluded"}
    assert preparation(accepted, value).status_code == 403


def test_exact_request_prepare_approval_and_immutable_intent(accepted):
    response = preparation(accepted, request(accepted, "post", FIELDS))
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    payload = json.loads(response.content)
    validate_contract(FIELDS, "Request")
    validate_contract(payload, "Execution")
    assert payload["intent_id"] == str(accepted[4].pk)
    existing = json.loads(preparation(accepted, request(accepted)).content)["execution"]
    assert existing["intent_id"] == payload["intent_id"]
    assert existing["operation_id"] == payload["operation_id"]
    detail = json.loads(draft_views.detail(request(accepted), payload["operation_id"]).content)
    assert detail["intent_id"] == payload["intent_id"]
    assert payload["status"] == "pending"
    assert payload["payload"]["event"]["listed"] is False
    operation_id = payload["operation_id"]
    response = draft_views.approve(
        request(
            accepted, "post", {"review_digest": "0" * 64, "revision_id": payload["revision_id"]}
        ),
        operation_id,
    )
    assert response.status_code == 403
    response = draft_views.approve(
        request(
            accepted,
            "post",
            {"review_digest": payload["review_digest"], "revision_id": payload["revision_id"]},
        ),
        operation_id,
    )
    assert response.status_code == 200
    assert json.loads(response.content)["status"] == "approved"
    assert json.loads(response.content)["intent_id"] == payload["intent_id"]
    assert DraftExecution.objects.get(pk=operation_id).receipt is None
    accepted[4].refresh_from_db()
    assert accepted[4].status == "pending" and accepted[4].approval_id is None
    value = request(accepted)
    value.user = accepted[2].user
    assert draft_views.detail(value, operation_id).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {**FIELDS, "action": "update"},
        {**FIELDS, "summary": "x" * 5000},
        {**FIELDS, "organization_id": "abc"},
        {**FIELDS, "currency": ""},
    ],
)
def test_strict_bounded_create_fields(accepted, body):
    assert preparation(accepted, request(accepted, "post", body)).status_code == 400
    assert not DraftExecution.objects.exists()


def test_csrf_required_for_prepare_and_actions(accepted):
    middleware = CsrfViewMiddleware(lambda request: None)
    for view in [
        draft_views.prepare,
        draft_views.approve,
        draft_views.execute,
        draft_views.reconcile,
    ]:
        response = middleware.process_view(request(accepted, "post", FIELDS), view, (), {})
        assert response.status_code == 403


@pytest.mark.parametrize("start", ["2027-03-14T02:30", "2027-11-07T01:30"])
def test_ambiguous_and_nonexistent_local_times_rejected(accepted, start):
    fields = {**FIELDS, "timezone": "America/New_York", "start_local": start}
    assert preparation(accepted, request(accepted, "post", fields)).status_code == 400
