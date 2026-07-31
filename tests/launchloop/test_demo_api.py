import json

import pytest
from django.test import Client


def post_json(
    client: Client,
    path: str,
    body: dict[str, object] | None = None,
    *,
    actor: str = "maya",
):
    return client.post(
        path,
        data=json.dumps(body or {}),
        content_type="application/json",
        headers={"X-Demo-Actor": actor},
    )


@pytest.mark.django_db
def test_complete_demo_journey_is_durable_and_four_eyes_approved() -> None:
    client = Client()

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
        actor="maya",
    )
    assert self_approval.status_code == 403
    assert self_approval.json()["code"] == "self_approval_forbidden"

    approved = post_json(
        client,
        f"/api/v1/approvals/{approval_id}/decision",
        {"decision": "approve", "package_hash": package_hash},
        actor="jordan",
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
def test_approval_rejects_a_stale_package_hash() -> None:
    client = Client()
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

    response = post_json(
        client,
        f"/api/v1/approvals/{submitted['approval']['id']}/decision",
        {"decision": "approve", "package_hash": "0" * 64},
        actor="jordan",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "package_hash_mismatch"
