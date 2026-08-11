import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.utils import timezone
from django_otp.oath import TOTP
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
    AdministratorTOTPDevice,
)
from identity.services.credentials import begin_totp_enrollment


def post_json(client: Client, path: str, body: dict[str, str]):
    return client.post(path, data=json.dumps(body), content_type="application/json")


@pytest.fixture(autouse=True)
def identity_configuration(settings, tmp_path: Path):
    encoded_key = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
    key_path = tmp_path / "identity-key-ring.json"
    key_path.write_text(
        json.dumps({"active_key_id": "synthetic-v1", "keys": {"synthetic-v1": encoded_key}}),
        encoding="utf-8",
    )
    settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED = True
    settings.CIVICLOOP_IDENTITY_KEY_FILE = key_path
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "identity-second-factor-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def active_owner(db):
    password = "Synthetic-Owner-Passphrase-934!"
    user = User.objects.create_user(username="synthetic.mfa.owner", password=password)
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    enrollment = begin_totp_enrollment(profile, label="Synthetic authenticator", now=timezone.now())
    enrollment.device.confirmed = True
    enrollment.device.confirmed_at = timezone.now()
    enrollment.device.save(update_fields=["confirmed", "confirmed_at"])
    return profile, password, enrollment.manual_secret


def current_token(manual_secret: str) -> str:
    padding = "=" * (-len(manual_secret) % 8)
    seed = base64.b32decode(manual_secret + padding)
    generator = TOTP(seed, 30, 0, 6, 0)
    generator.time = timezone.now().timestamp()
    return f"{generator.token():06d}"


def password_challenge(client: Client, profile: AdministratorProfile, password: str):
    return post_json(
        client,
        "/api/v1/admin/auth/password",
        {"username": profile.user.username, "password": password},
    )


def test_totp_requires_valid_password_preauthentication(db) -> None:
    response = post_json(Client(), "/api/v1/admin/auth/totp", {"token": "123456"})

    assert response.status_code == 401
    assert response.json()["code"] == "preauthentication_required"


def test_totp_success_establishes_full_session_and_rotates_key(active_owner) -> None:
    profile, password, secret = active_owner
    client = Client()
    assert password_challenge(client, profile, password).status_code == 200
    preauth_key = client.session.session_key

    response = post_json(
        client,
        "/api/v1/admin/auth/totp",
        {"token": current_token(secret)},
    )

    assert response.status_code == 200
    assert response.json() == {"stage": "authenticated"}
    assert response["Cache-Control"] == "no-store"
    assert client.session.session_key != preauth_key
    assert "civicloop_admin_preauth" not in client.session
    assert "_auth_user_id" in client.session
    metadata = AdministratorSession.objects.get(profile=profile)
    assert metadata.session_key == client.session.session_key
    assert metadata.recovery_restricted is False
    assert metadata.mfa_verified_at is not None
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="owner_totp_verified",
        outcome="success",
    ).exists()


def test_totp_invalid_and_replayed_tokens_are_rejected(active_owner) -> None:
    profile, password, secret = active_owner
    invalid_client = Client()
    password_challenge(invalid_client, profile, password)
    token = current_token(secret)
    invalid_token = f"{(int(token) + 1) % 1_000_000:06d}"
    invalid = post_json(
        invalid_client,
        "/api/v1/admin/auth/totp",
        {"token": invalid_token},
    )
    assert invalid.status_code == 401
    assert invalid.json()["code"] == "verification_failed"
    device = AdministratorTOTPDevice.objects.get(profile=profile)
    device.throttling_failure_timestamp -= timedelta(seconds=2)
    device.save(update_fields=["throttling_failure_timestamp"])

    first_client = Client()
    password_challenge(first_client, profile, password)
    assert post_json(first_client, "/api/v1/admin/auth/totp", {"token": token}).status_code == 200
    post_json(first_client, "/api/v1/admin/auth/logout", {})
    replay_client = Client()
    password_challenge(replay_client, profile, password)
    replay = post_json(replay_client, "/api/v1/admin/auth/totp", {"token": token})

    assert replay.status_code == 401
    assert replay.json()["code"] == "verification_failed"


def test_logout_revokes_metadata_and_flushes_session(active_owner) -> None:
    profile, password, secret = active_owner
    client = Client()
    password_challenge(client, profile, password)
    post_json(client, "/api/v1/admin/auth/totp", {"token": current_token(secret)})
    metadata = AdministratorSession.objects.get(profile=profile)

    response = post_json(client, "/api/v1/admin/auth/logout", {})

    assert response.status_code == 200
    assert response.json() == {"stage": "anonymous"}
    assert not client.session.items()
    metadata.refresh_from_db()
    assert metadata.revoked_at is not None
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="owner_logout",
        outcome="success",
    ).exists()
