import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client
from django.utils import timezone
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
    RecoveryCode,
)
from identity.services.credentials import generate_recovery_batch

from .test_second_factor_auth_api import identity_configuration  # noqa: F401


def post_json(client: Client, path: str, body: dict[str, str]):
    return client.post(path, data=json.dumps(body), content_type="application/json")


@pytest.fixture
def owner_with_recovery_codes(db):
    password = "Synthetic-Recovery-Passphrase-934!"
    user = User.objects.create_user(username="synthetic.recovery.owner", password=password)
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    codes = generate_recovery_batch(profile, now=timezone.now())
    return profile, password, codes


def password_challenge(client: Client, profile: AdministratorProfile, password: str):
    return post_json(
        client,
        "/api/v1/admin/auth/password",
        {"username": profile.user.username, "password": password},
    )


def test_recovery_requires_password_preauthentication(db) -> None:
    response = post_json(
        Client(),
        "/api/v1/admin/auth/recovery",
        {"recovery_code": "AAAAAAAA-AAAAAAAAAAAAAAAAAAAAAAAAAA"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "preauthentication_required"


def test_recovery_consumes_code_revokes_other_sessions_and_restricts_access(
    owner_with_recovery_codes,
) -> None:
    profile, password, codes = owner_with_recovery_codes
    old_django = Session.objects.create(
        session_key="synthetic-old-admin-session",
        session_data="",
        expire_date=timezone.now() + timedelta(hours=1),
    )
    old_metadata = AdministratorSession.objects.create(
        profile=profile,
        session_key=old_django.session_key,
        authenticated_at=timezone.now(),
        last_activity_at=timezone.now(),
        mfa_verified_at=timezone.now(),
        absolute_expires_at=timezone.now() + timedelta(hours=12),
        expires_at=timezone.now() + timedelta(minutes=30),
        device_label="Old browser",
    )
    client = Client()
    password_challenge(client, profile, password)

    response = post_json(
        client,
        "/api/v1/admin/auth/recovery",
        {"recovery_code": codes[0]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "stage": "recovery_restricted",
        "next_action": "replace_totp",
    }
    profile.refresh_from_db()
    assert profile.status == AdministratorProfile.Status.RECOVERY_REQUIRED
    assert RecoveryCode.objects.get(public_id=codes[0][:8]).consumed_at is not None
    current = AdministratorSession.objects.exclude(pk=old_metadata.pk).get(profile=profile)
    assert current.recovery_restricted is True
    assert current.mfa_verified_at is None
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="owner_recovery_verified",
        outcome="success",
    ).exists()
    old_metadata.refresh_from_db()
    assert old_metadata.revoked_at is not None
    assert not Session.objects.filter(pk=old_django.pk).exists()
    denied = client.get("/api/v1/demo")
    assert denied.status_code == 403
    status = client.get("/api/v1/admin/security/status")
    assert status.json()["stage"] == "recovery_restricted"


def test_recovery_code_cannot_be_reused(owner_with_recovery_codes) -> None:
    profile, password, codes = owner_with_recovery_codes
    first = Client()
    password_challenge(first, profile, password)
    assert post_json(
        first,
        "/api/v1/admin/auth/recovery",
        {"recovery_code": codes[0]},
    ).status_code == 200
    post_json(first, "/api/v1/admin/auth/logout", {})
    profile.status = AdministratorProfile.Status.ACTIVE
    profile.save(update_fields=["status", "updated_at"])
    second = Client()
    password_challenge(second, profile, password)

    reused = post_json(
        second,
        "/api/v1/admin/auth/recovery",
        {"recovery_code": codes[0]},
    )

    assert reused.status_code == 401
    assert reused.json()["code"] == "verification_failed"
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="owner_recovery_verified",
        outcome="failure",
    ).exists()


def test_demo_login_never_satisfies_owner_recovery_preauthentication(db) -> None:
    client = Client()
    demo_login = post_json(
        client,
        "/api/v1/auth/login",
        {"username": "maya.operator", "password": "civicloop-demo"},
    )
    assert demo_login.status_code == 200

    response = post_json(
        client,
        "/api/v1/admin/auth/recovery",
        {"recovery_code": "AAAAAAAA-AAAAAAAAAAAAAAAAAAAAAAAAAA"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "preauthentication_required"
