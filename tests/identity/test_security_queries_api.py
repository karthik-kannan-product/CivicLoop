import pytest
from django.test import Client
from identity.models import AdministratorSecurityEvent
from identity.services.security import record_security_event

from .test_second_factor_auth_api import identity_configuration  # noqa: F401
from .test_security_actions_api import create_authenticated_owner


@pytest.fixture
def authenticated_owner(db):
    return create_authenticated_owner()


def test_session_query_exposes_only_bounded_public_fields(authenticated_owner) -> None:
    client, _profile, metadata, _password = authenticated_owner
    metadata.user_agent = "Synthetic Secret User Agent"
    metadata.save(update_fields=["user_agent"])

    response = client.get("/api/v1/admin/security/sessions")

    assert response.status_code == 200
    row = response.json()["sessions"][0]
    assert set(row) == {
        "id",
        "device_label",
        "source_ip",
        "created_at",
        "authenticated_at",
        "last_activity_at",
        "mfa_verified_at",
        "absolute_expires_at",
        "expires_at",
        "revoked_at",
        "is_current",
    }
    assert row["is_current"] is True
    serialized = response.content.decode()
    assert metadata.session_key not in serialized
    assert "Synthetic Secret User Agent" not in serialized


def test_security_events_use_signed_cursor_pagination_and_redacted_fields(
    authenticated_owner,
) -> None:
    client, profile, metadata, _password = authenticated_owner
    for index in range(3):
        record_security_event(
            action=f"synthetic_event_{index}",
            outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
            owner=profile,
            source_ip="192.0.2.44",
            session_id=metadata.id,
            details={"count": index},
        )

    first = client.get("/api/v1/admin/security/events?limit=2")
    second = client.get(
        "/api/v1/admin/security/events",
        {"limit": 2, "cursor": first.json()["next_cursor"]},
    )

    assert first.status_code == 200
    assert len(first.json()["events"]) == 2
    assert len(second.json()["events"]) == 1
    assert first.json()["next_cursor"] is not None
    assert second.json()["next_cursor"] is None
    row = first.json()["events"][0]
    assert set(row) == {
        "id",
        "action",
        "outcome",
        "target_type",
        "target_id",
        "details",
        "source_ip",
        "session_id",
        "created_at",
    }


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=nope", "cursor=tampered"])
def test_security_event_query_rejects_invalid_pagination(authenticated_owner, query: str) -> None:
    client, _profile, _metadata, _password = authenticated_owner
    response = client.get(f"/api/v1/admin/security/events?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_pagination"


def test_queries_require_full_administrator_session(db) -> None:
    assert Client().get("/api/v1/admin/security/sessions").status_code == 401
    assert Client().get("/api/v1/admin/security/events").status_code == 401
