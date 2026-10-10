import json

import pytest
from django.test import Client, override_settings

from tests.identity.test_security_actions_api import create_authenticated_owner

FEATURES = override_settings(
    CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
    CIVICLOOP_INTEGRATIONS_ENABLED=True,
)


@pytest.mark.django_db
@FEATURES
def test_eventbrite_list_requires_an_authenticated_administrator() -> None:
    assert Client().get("/api/v1/eventbrite/events").status_code == 401

    client, _profile, _metadata, _password = create_authenticated_owner()
    response = client.get("/api/v1/eventbrite/events")

    assert response.status_code == 200
    assert response.json() == {
        "events": [],
        "next_cursor": None,
        "has_more": False,
        "complete": False,
    }

    reset = client.post("/api/v1/demo/reset")
    assert reset.status_code == 403
    assert reset.json()["code"] == "demo_only"


@pytest.mark.django_db
def test_operator_can_start_a_manual_event_without_provider_access() -> None:
    client = Client()
    login = client.post(
        "/api/v1/auth/login",
        data=json.dumps({"username": "maya.operator", "password": "civicloop-demo"}),
        content_type="application/json",
    )
    assert login.status_code == 200

    response = client.post(
        "/api/v1/events/manual",
        data=json.dumps(
            {
                "title": "Volunteer Night",
                "date": "2026-10-20",
                "timezone": "America/Toronto",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["event"]["title"] == "Volunteer Night"
    assert response.json()["workflow"]["status"] == "draft"


@pytest.mark.django_db
@FEATURES
def test_eventbrite_refresh_returns_page_state_and_forwards_cursor(monkeypatch) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    observed = []

    def refresh(*, administrator, cursor):
        observed.append(cursor)
        return {"events": [], "next_cursor": "opaque-next", "has_more": True, "complete": False}

    monkeypatch.setattr("launchloop.views.refresh_configured_eventbrite_events", refresh)
    response = client.post("/api/v1/eventbrite/events/refresh?cursor=opaque-current")
    assert response.status_code == 200
    assert observed == ["opaque-current"]
    assert response.json() == {
        "events": [],
        "next_cursor": "opaque-next",
        "has_more": True,
        "complete": False,
    }
