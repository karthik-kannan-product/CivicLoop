import json

import pytest
from django.contrib.auth.models import User
from django.test import Client


def post_json(
    client: Client,
    path: str,
    body: dict[str, object] | None = None,
):
    return client.post(
        path,
        data=json.dumps(body or {}),
        content_type="application/json",
    )


def login(client: Client, username: str) -> None:
    response = post_json(
        client,
        "/api/v1/auth/login",
        {"username": username, "password": "civicloop-demo"},
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_complete_demo_journey_is_durable_and_four_eyes_approved() -> None:
    client = Client()

    login(client, "maya.operator")
    reset = post_json(client, "/api/v1/demo/reset")
    assert reset.status_code == 200
    initial = reset.json()
    workflow_id = initial["workflow"]["id"]
    assert initial["event"]["revision"]["version"] == 1
    assert initial["workflow"]["status"] == "draft"

    blocked = post_json(client, f"/api/v1/workflows/{workflow_id}/runs")
    assert blocked.status_code == 200
    assert blocked.json()["workflow"]["status"] == "needs_input"
    assert blocked.json()["workflow"]["package"]["missing_fields"] == [
        "venue_name",
        "venue_address",
        "access_instructions",
    ]

    resolved = post_json(
        client,
        f"/api/v1/workflows/{workflow_id}/answers",
        {
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street, New York, NY 10001",
            "access_instructions": (
                "Use the 10th Avenue entrance and check in at the nonprofit events desk."
            ),
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["event"]["revision"]["version"] == 2
    assert resolved.json()["workflow"]["status"] == "draft"

    ready = post_json(client, f"/api/v1/workflows/{workflow_id}/runs")
    assert ready.status_code == 200
    ready_state = ready.json()
    assert ready_state["workflow"]["status"] == "ready_for_review"
    assert ready_state["workflow"]["package"]["audience"]["member_count"] == 418
    assert ready_state["workflow"]["package"]["sponsor"]["expected_discount_percent"] == 25

    submitted = post_json(client, f"/api/v1/workflows/{workflow_id}/submit")
    assert submitted.status_code == 200
    approval_id = submitted.json()["approval"]["id"]
    package_hash = submitted.json()["approval"]["package_hash"]
    assert submitted.json()["workflow"]["status"] == "in_review"

    self_approval = post_json(
        client,
        f"/api/v1/approvals/{approval_id}/decision",
        {"decision": "approve", "package_hash": package_hash},
    )
    assert self_approval.status_code == 403
    assert self_approval.json()["code"] == "self_approval_forbidden"

    assert post_json(client, "/api/v1/auth/logout").status_code == 200
    login(client, "jordan.approver")
    approved = post_json(
        client,
        f"/api/v1/approvals/{approval_id}/decision",
        {"decision": "approve", "package_hash": package_hash},
    )
    assert approved.status_code == 200
    completed = approved.json()
    assert completed["workflow"]["status"] == "completed"
    assert completed["approval"]["status"] == "approved"
    assert completed["execution"]["status"] == "delivered"
    assert completed["execution"]["receipt"]["audience_count"] == 418

    reloaded = client.get("/api/v1/demo")
    assert reloaded.status_code == 200
    assert reloaded.json()["workflow"]["status"] == "completed"
    assert reloaded.json()["execution"]["id"] == completed["execution"]["id"]
    assert len(reloaded.json()["timeline"]) >= 7


@pytest.mark.django_db
def test_operator_can_save_partial_event_facts_before_completing_input() -> None:
    client = Client()

    login(client, "maya.operator")
    initial = post_json(client, "/api/v1/demo/reset").json()
    workflow_id = initial["workflow"]["id"]
    post_json(client, f"/api/v1/workflows/{workflow_id}/runs")

    partial = post_json(
        client,
        f"/api/v1/workflows/{workflow_id}/answers",
        {
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street, New York, NY 10001",
        },
    )

    assert partial.status_code == 200
    partial_state = partial.json()
    assert partial_state["workflow"]["status"] == "needs_input"
    assert partial_state["event"]["revision"]["version"] == 2
    assert partial_state["event"]["revision"]["facts"]["venue_name"] == "Hudson Civic Center"
    assert partial_state["event"]["revision"]["facts"]["venue_address"] == (
        "455 West 34th Street, New York, NY 10001"
    )
    assert partial_state["event"]["revision"]["facts"]["access_instructions"] == ""

    assert post_json(client, "/api/v1/auth/logout").status_code == 200
    login(client, "maya.operator")
    reloaded = client.get("/api/v1/demo")

    assert reloaded.status_code == 200
    assert reloaded.json()["workflow"]["status"] == "needs_input"
    assert reloaded.json()["event"]["revision"]["facts"]["venue_name"] == "Hudson Civic Center"

    completed = post_json(
        client,
        f"/api/v1/workflows/{workflow_id}/answers",
        {"access_instructions": "Use the 10th Avenue entrance."},
    )

    assert completed.status_code == 200
    assert completed.json()["workflow"]["status"] == "draft"
    assert completed.json()["event"]["revision"]["version"] == 3


@pytest.mark.django_db
def test_approver_can_reject_and_reopen_work_for_operator() -> None:
    client = Client()
    login(client, "maya.operator")
    initial = post_json(client, "/api/v1/demo/reset").json()
    workflow_id = initial["workflow"]["id"]
    post_json(client, f"/api/v1/workflows/{workflow_id}/runs")
    post_json(
        client,
        f"/api/v1/workflows/{workflow_id}/answers",
        {
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street, New York, NY 10001",
            "access_instructions": "Use the 10th Avenue entrance.",
        },
    )
    post_json(client, f"/api/v1/workflows/{workflow_id}/runs")
    submitted = post_json(client, f"/api/v1/workflows/{workflow_id}/submit").json()

    assert post_json(client, "/api/v1/auth/logout").status_code == 200
    login(client, "jordan.approver")
    rejected = post_json(
        client,
        f"/api/v1/approvals/{submitted['approval']['id']}/decision",
        {
            "decision": "reject",
            "package_hash": submitted["approval"]["package_hash"],
            "reason": "Please add wheelchair-accessible entrance instructions.",
        },
    )

    assert rejected.status_code == 200
    rejected_state = rejected.json()
    assert rejected_state["workflow"]["status"] == "needs_input"
    assert rejected_state["workflow"]["package"] is None
    assert rejected_state["workflow"]["package_hash"] is None
    assert rejected_state["approval"]["status"] == "rejected"
    assert rejected_state["approval"]["reason"] == (
        "Please add wheelchair-accessible entrance instructions."
    )

    assert post_json(client, "/api/v1/auth/logout").status_code == 200
    login(client, "maya.operator")
    updated = post_json(
        client,
        f"/api/v1/workflows/{workflow_id}/answers",
        {"access_instructions": "Use the wheelchair-accessible 10th Avenue entrance."},
    )

    assert updated.status_code == 200
    assert updated.json()["workflow"]["status"] == "draft"
    assert updated.json()["approval"] is None


@pytest.mark.django_db
def test_approval_rejects_a_stale_package_hash() -> None:
    client = Client()
    login(client, "maya.operator")
    initial = post_json(client, "/api/v1/demo/reset").json()
    workflow_id = initial["workflow"]["id"]
    post_json(client, f"/api/v1/workflows/{workflow_id}/runs")
    post_json(
        client,
        f"/api/v1/workflows/{workflow_id}/answers",
        {
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street, New York, NY 10001",
            "access_instructions": "Use the 10th Avenue entrance.",
        },
    )
    post_json(client, f"/api/v1/workflows/{workflow_id}/runs")
    submitted = post_json(client, f"/api/v1/workflows/{workflow_id}/submit").json()

    assert post_json(client, "/api/v1/auth/logout").status_code == 200
    login(client, "jordan.approver")
    response = post_json(
        client,
        f"/api/v1/approvals/{submitted['approval']['id']}/decision",
        {"decision": "approve", "package_hash": "0" * 64},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "package_hash_mismatch"


@pytest.mark.django_db
def test_demo_requires_a_logged_in_user_and_exposes_seeded_session_identity() -> None:
    client = Client()

    assert client.get("/api/v1/demo").status_code == 401

    login(client, "maya.operator")
    session = client.get("/api/v1/auth/session")

    assert session.status_code == 200
    assert session.json()["user"] == {
        "username": "maya.operator",
        "display_name": "Maya Chen",
        "role": "operator",
    }
    assert User.objects.filter(username="jordan.approver").exists()


@pytest.mark.django_db
def test_approver_cannot_change_event_facts_or_run_agents() -> None:
    client = Client()
    login(client, "maya.operator")
    workflow_id = post_json(client, "/api/v1/demo/reset").json()["workflow"]["id"]
    assert post_json(client, "/api/v1/auth/logout").status_code == 200
    login(client, "jordan.approver")

    response = post_json(client, f"/api/v1/workflows/{workflow_id}/runs")

    assert response.status_code == 403
    assert response.json()["code"] == "operator_required"


@pytest.mark.django_db
def test_api_errors_use_problem_details_with_compatibility_extensions() -> None:
    client = Client()

    response = client.get("/api/v1/demo")

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "urn:civicloop:problem:authentication_required",
        "title": "Authentication required",
        "status": 401,
        "detail": "Sign in to use the demo workspace.",
        "instance": "/api/v1/demo",
        "code": "authentication_required",
        "message": "Sign in to use the demo workspace.",
    }
