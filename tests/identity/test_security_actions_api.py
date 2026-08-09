import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client
from django.utils import timezone
from identity.exceptions import IdentityError
from identity.models import AdministratorProfile, AdministratorSession, RecoveryCode
from identity.services.sessions import ADMIN_SESSION_KEY

from .test_second_factor_auth_api import identity_configuration  # noqa: F401


def request_json(client: Client, method: str, path: str, body: dict[str, str] | None = None):
    return getattr(client, method)(
        path,
        data=json.dumps(body or {}),
        content_type="application/json",
    )


def create_authenticated_owner():
    password = "Synthetic-Security-Passphrase-934!"
    user = User.objects.create_user(username="synthetic.security.owner", password=password)
    profile = AdministratorProfile.objects.create(user=user, status="active")
    client = Client()
    client.force_login(user)
    session = client.session
    now = timezone.now()
    metadata = AdministratorSession.objects.create(
        profile=profile,
        session_key=session.session_key,
        authenticated_at=now,
        last_activity_at=now,
        mfa_verified_at=now,
        fresh_verified_at=now,
        absolute_expires_at=now + timedelta(hours=12),
        expires_at=now + timedelta(minutes=30),
        device_label="Synthetic Browser",
        source_ip="192.0.2.44",
    )
    session[ADMIN_SESSION_KEY] = str(metadata.id)
    session.save()
    return client, profile, metadata, password


@pytest.fixture
def authenticated_owner(db):
    return create_authenticated_owner()


def add_other_session(profile: AdministratorProfile) -> AdministratorSession:
    django_session = Session.objects.create(
        session_key=f"synthetic-other-{AdministratorSession.objects.count()}",
        session_data="",
        expire_date=timezone.now() + timedelta(hours=1),
    )
    return AdministratorSession.objects.create(
        profile=profile,
        session_key=django_session.session_key,
        authenticated_at=timezone.now(),
        last_activity_at=timezone.now(),
        mfa_verified_at=timezone.now(),
        absolute_expires_at=timezone.now() + timedelta(hours=1),
        expires_at=timezone.now() + timedelta(minutes=30),
        device_label="Other browser",
    )


def test_sensitive_actions_require_fresh_verification(authenticated_owner) -> None:
    client, _profile, metadata, password = authenticated_owner
    metadata.fresh_verified_at = timezone.now() - timedelta(minutes=10)
    metadata.save(update_fields=["fresh_verified_at"])

    password_response = request_json(
        client,
        "put",
        "/api/v1/admin/security/password",
        {"current_password": password, "new_password": "Synthetic-New-Passphrase-935!"},
    )
    recovery_response = request_json(
        client,
        "post",
        "/api/v1/admin/security/recovery-codes/regeneration",
    )

    assert password_response.status_code == 403
    assert recovery_response.status_code == 403
    assert password_response.json()["code"] == "fresh_verification_required"


def test_password_change_validates_rotates_and_revokes_others(authenticated_owner) -> None:
    client, profile, metadata, password = authenticated_owner
    other = add_other_session(profile)
    old_key = metadata.session_key

    mismatch = request_json(
        client,
        "put",
        "/api/v1/admin/security/password",
        {"current_password": "wrong-password", "new_password": "Synthetic-New-Passphrase-935!"},
    )
    weak = request_json(
        client,
        "put",
        "/api/v1/admin/security/password",
        {"current_password": password, "new_password": "short"},
    )
    success = request_json(
        client,
        "put",
        "/api/v1/admin/security/password",
        {"current_password": password, "new_password": "Synthetic-New-Passphrase-935!"},
    )

    assert mismatch.status_code == 401
    assert weak.status_code == 400
    assert success.status_code == 200
    profile.user.refresh_from_db()
    assert profile.user.check_password("Synthetic-New-Passphrase-935!")
    metadata.refresh_from_db()
    other.refresh_from_db()
    assert metadata.session_key != old_key
    assert metadata.fresh_verified_at is None
    assert other.revoked_at is not None


def test_recovery_regeneration_returns_codes_once_and_revokes_others(
    authenticated_owner,
) -> None:
    client, profile, metadata, _password = authenticated_owner
    other = add_other_session(profile)

    response = request_json(
        client,
        "post",
        "/api/v1/admin/security/recovery-codes/regeneration",
    )

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    codes = response.json()["recovery_codes"]
    assert len(codes) == 10
    assert not any(
        code == row.encoded_secret
        for code in codes
        for row in RecoveryCode.objects.all()
    )
    metadata.refresh_from_db()
    other.refresh_from_db()
    assert metadata.fresh_verified_at is None
    assert other.revoked_at is not None


def test_single_session_revocation_and_self_revocation(authenticated_owner) -> None:
    client, profile, metadata, _password = authenticated_owner
    other = add_other_session(profile)

    other_response = request_json(
        client,
        "post",
        f"/api/v1/admin/security/sessions/{other.id}/revocation",
    )
    self_response = request_json(
        client,
        "post",
        f"/api/v1/admin/security/sessions/{metadata.id}/revocation",
    )

    assert other_response.status_code == 200
    assert other_response.json() == {"revoked": True, "logged_out": False}
    assert self_response.status_code == 200
    assert self_response.json() == {"revoked": True, "logged_out": True}
    assert not client.session.items()


def test_revoke_others_requires_freshness_and_preserves_current(authenticated_owner) -> None:
    client, profile, metadata, _password = authenticated_owner
    other = add_other_session(profile)

    response = request_json(
        client,
        "post",
        "/api/v1/admin/security/sessions/revoke-others",
    )

    assert response.status_code == 200
    assert response.json() == {"revoked_count": 1}
    metadata.refresh_from_db()
    other.refresh_from_db()
    assert metadata.revoked_at is None
    assert metadata.fresh_verified_at is None
    assert other.revoked_at is not None


def test_unknown_session_revocation_is_redacted(authenticated_owner) -> None:
    client, _profile, _metadata, _password = authenticated_owner
    response = request_json(
        client,
        "post",
        "/api/v1/admin/security/sessions/00000000-0000-4000-8000-000000000001/revocation",
    )

    assert response.status_code == 404
    assert response.json()["code"] == "session_not_found"


def test_mandatory_audit_failure_rolls_back_password_change(
    authenticated_owner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profile, _metadata, password = authenticated_owner
    other = add_other_session(profile)

    def unavailable_audit(**kwargs):
        raise IdentityError("synthetic audit outage")

    monkeypatch.setattr(
        "identity.services.security_actions.record_security_event",
        unavailable_audit,
    )
    client.raise_request_exception = False
    response = request_json(
        client,
        "put",
        "/api/v1/admin/security/password",
        {"current_password": password, "new_password": "Synthetic-New-Passphrase-935!"},
    )

    assert response.status_code == 500
    profile.user.refresh_from_db()
    other.refresh_from_db()
    assert profile.user.check_password(password)
    assert other.revoked_at is None
