import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone
from identity.models import AdministratorProfile, AdministratorSecurityEvent

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "identity-password-api-tests",
    }
}
ADMIN_ENABLED = override_settings(
    CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
    CACHES=LOCMEM_CACHE,
)


def post_json(client: Client, path: str, body: dict[str, str], **headers):
    return client.post(path, data=json.dumps(body), content_type="application/json", **headers)


@pytest.fixture(autouse=True)
def clear_test_cache():
    with override_settings(CACHES=LOCMEM_CACHE):
        cache.clear()
        yield
        cache.clear()


@pytest.fixture
def owner_profile(db) -> AdministratorProfile:
    user = User.objects.create_user(
        username="synthetic.api.owner",
        password="Synthetic-Owner-Passphrase-934!",
    )
    return AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
    )


@ADMIN_ENABLED
def test_status_sets_csrf_cookie_for_anonymous_browser(db) -> None:
    response = Client().get("/api/v1/admin/security/status")

    assert response.status_code == 200
    assert response.json() == {"stage": "anonymous"}
    assert "csrftoken" in response.cookies
    assert response["Cache-Control"] == "no-store"


@ADMIN_ENABLED
def test_password_success_creates_only_bounded_preauthentication_state(
    owner_profile: AdministratorProfile,
) -> None:
    client = Client()
    initial_session = client.session
    initial_session["untrusted_previous_state"] = "remove-me"
    initial_session.save()
    previous_key = initial_session.session_key

    response = post_json(
        client,
        "/api/v1/admin/auth/password",
        {
            "username": " synthetic.api.owner ",
            "password": "Synthetic-Owner-Passphrase-934!",
        },
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "password_verified"
    assert response.json()["next_action"] == "enroll_totp"
    assert response["Cache-Control"] == "no-store"
    session = client.session
    assert session.session_key != previous_key
    assert "_auth_user_id" not in session
    assert "untrusted_previous_state" not in session
    assert set(session["civicloop_admin_preauth"]) == {
        "owner_id",
        "stage",
        "issued_at",
        "expires_at",
        "correlation_id",
    }
    assert "Synthetic-Owner-Passphrase-934!" not in json.dumps(dict(session))
    assert AdministratorSecurityEvent.objects.filter(
        action="owner_password_verified",
        outcome="success",
        profile=owner_profile,
    ).exists()


@ADMIN_ENABLED
def test_active_owner_password_challenge_requires_totp_next(
    owner_profile: AdministratorProfile,
) -> None:
    owner_profile.status = AdministratorProfile.Status.ACTIVE
    owner_profile.save(update_fields=["status", "updated_at"])

    response = post_json(
        Client(),
        "/api/v1/admin/auth/password",
        {
            "username": "synthetic.api.owner",
            "password": "Synthetic-Owner-Passphrase-934!",
        },
    )

    assert response.status_code == 200
    assert response.json()["next_action"] == "verify_totp"


@ADMIN_ENABLED
def test_unknown_owner_wrong_password_and_demo_user_are_indistinguishable(
    owner_profile: AdministratorProfile,
) -> None:
    demo = User.objects.create_user(
        username="synthetic.demo.user",
        password="Synthetic-Demo-Passphrase-934!",
    )
    cases = [
        {"username": "missing.owner", "password": "Synthetic-Owner-Passphrase-934!"},
        {"username": owner_profile.user.username, "password": "wrong-password"},
        {"username": demo.username, "password": "Synthetic-Demo-Passphrase-934!"},
    ]

    responses = [post_json(Client(), "/api/v1/admin/auth/password", body) for body in cases]

    assert {response.status_code for response in responses} == {401}
    assert len({json.dumps(response.json(), sort_keys=True) for response in responses}) == 1
    assert responses[0].json()["code"] == "invalid_credentials"
    assert all("username" not in response.content.decode().lower() for response in responses)


@ADMIN_ENABLED
def test_disabled_owner_cannot_create_preauthentication_state(
    owner_profile: AdministratorProfile,
) -> None:
    owner_profile.status = AdministratorProfile.Status.DISABLED
    owner_profile.save(update_fields=["status", "updated_at"])
    client = Client()

    response = post_json(
        client,
        "/api/v1/admin/auth/password",
        {
            "username": owner_profile.user.username,
            "password": "Synthetic-Owner-Passphrase-934!",
        },
    )

    assert response.status_code == 401
    assert "civicloop_admin_preauth" not in client.session


@ADMIN_ENABLED
def test_expired_preauthentication_status_is_anonymous(owner_profile: AdministratorProfile) -> None:
    client = Client()
    session = client.session
    session["civicloop_admin_preauth"] = {
        "owner_id": str(owner_profile.id),
        "stage": "password_verified",
        "issued_at": (timezone.now() - timedelta(minutes=10)).isoformat(),
        "expires_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
        "correlation_id": "c1ddebee-1291-4209-9ea9-4d87212cc33c",
    }
    session.save()

    response = client.get("/api/v1/admin/security/status")

    assert response.status_code == 200
    assert response.json() == {"stage": "anonymous"}
    assert "civicloop_admin_preauth" not in client.session


@ADMIN_ENABLED
def test_password_endpoint_requires_csrf(owner_profile: AdministratorProfile) -> None:
    client = Client(enforce_csrf_checks=True)
    status = client.get("/api/v1/admin/security/status")
    csrf_token = status.cookies["csrftoken"].value
    body = {
        "username": owner_profile.user.username,
        "password": "Synthetic-Owner-Passphrase-934!",
    }

    rejected = post_json(client, "/api/v1/admin/auth/password", body)
    accepted = post_json(
        client,
        "/api/v1/admin/auth/password",
        body,
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200


@ADMIN_ENABLED
def test_password_endpoint_rate_limits_sixth_attempt(owner_profile: AdministratorProfile) -> None:
    client = Client(REMOTE_ADDR="192.0.2.10")
    body = {"username": owner_profile.user.username, "password": "wrong-password"}
    responses = [post_json(client, "/api/v1/admin/auth/password", body) for _ in range(6)]

    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert responses[5]["Retry-After"] == "300"


@ADMIN_ENABLED
def test_password_endpoint_fails_closed_when_rate_limit_backend_is_unavailable(
    owner_profile: AdministratorProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic valkey detail")

    monkeypatch.setattr("identity.rate_limits.cache.add", unavailable)

    response = post_json(
        Client(),
        "/api/v1/admin/auth/password",
        {
            "username": owner_profile.user.username,
            "password": "Synthetic-Owner-Passphrase-934!",
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "identity_unavailable"
    assert "valkey" not in response.content.decode().lower()


@override_settings(CIVICLOOP_ADMIN_IDENTITY_ENABLED=False)
def test_administrator_api_is_not_exposed_when_feature_is_disabled(db) -> None:
    response = Client().get("/api/v1/admin/security/status")

    assert response.status_code == 404
