import base64
import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client
from django.utils import timezone
from django_otp.oath import TOTP
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
    AdministratorTOTPDevice,
    RecoveryCode,
)
from identity.services.credentials import begin_totp_enrollment, generate_recovery_batch
from identity.services.sessions import administrator_session_is_fresh

from .test_second_factor_auth_api import identity_configuration  # noqa: F401


def post_json(client: Client, path: str, body: dict[str, str]):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def token_for(manual_secret: str, *, offset_seconds: int = 0) -> str:
    padding = "=" * (-len(manual_secret) % 8)
    generator = TOTP(base64.b32decode(manual_secret + padding), 30, 0, 6, 0)
    generator.time = (timezone.now() + timedelta(seconds=offset_seconds)).timestamp()
    return f"{generator.token():06d}"


@pytest.fixture
def enrollment_owner(db):
    password = "Synthetic-Enrollment-Passphrase-934!"
    user = User.objects.create_user(username="synthetic.enrollment.owner", password=password)
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
    )
    return profile, password


def password_challenge(client: Client, profile: AdministratorProfile, password: str):
    return post_json(
        client,
        "/api/v1/admin/auth/password",
        {"username": profile.user.username, "password": password},
    )


def begin_enrollment(client: Client):
    return post_json(
        client,
        "/api/v1/admin/security/totp/enrollment",
        {"label": "Synthetic authenticator"},
    )


def test_enrollment_requires_password_or_recovery_state(db) -> None:
    response = begin_enrollment(Client())
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_enrollment_returns_one_time_provisioning_material_with_no_store(
    enrollment_owner,
) -> None:
    profile, password = enrollment_owner
    client = Client()
    password_challenge(client, profile, password)

    first = begin_enrollment(client)
    second = begin_enrollment(client)

    assert first.status_code == 200
    assert first["Cache-Control"] == "no-store"
    assert set(first.json()) == {"device_id", "otpauth_uri", "manual_secret"}
    assert first.json()["otpauth_uri"].startswith("otpauth://totp/CivicLoop%3A")
    assert "issuer=CivicLoop" in first.json()["otpauth_uri"]
    assert first.json()["manual_secret"] not in second.content.decode()
    assert first.json()["device_id"] != second.json()["device_id"]
    assert first.json()["manual_secret"] not in json.dumps(
        list(AdministratorSecurityEvent.objects.values_list("details", flat=True))
    )


def test_initial_confirmation_establishes_full_session_and_returns_ten_codes_once(
    enrollment_owner,
) -> None:
    profile, password = enrollment_owner
    client = Client()
    password_challenge(client, profile, password)
    enrollment = begin_enrollment(client).json()

    response = post_json(
        client,
        "/api/v1/admin/security/totp/confirmation",
        {
            "device_id": enrollment["device_id"],
            "token": token_for(enrollment["manual_secret"]),
        },
    )

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json()["stage"] == "authenticated"
    codes = response.json()["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10
    profile.refresh_from_db()
    assert profile.status == AdministratorProfile.Status.ACTIVE
    metadata = AdministratorSession.objects.get(profile=profile)
    assert metadata.recovery_restricted is False
    assert metadata.mfa_verified_at is not None
    stored = json.dumps(list(RecoveryCode.objects.values()), default=str)
    assert not any(code in stored for code in codes)
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="owner_totp_enrollment_confirmed",
        outcome="success",
    ).exists()


def test_confirmation_rejects_device_owned_by_another_profile(
    enrollment_owner,
) -> None:
    profile, password = enrollment_owner
    client = Client()
    password_challenge(client, profile, password)
    other_user = User.objects.create_user(username="synthetic.disabled.other")
    other_profile = AdministratorProfile.objects.create(
        user=other_user,
        status=AdministratorProfile.Status.DISABLED,
    )
    other_device = AdministratorTOTPDevice.objects.create(
        user=other_user,
        profile=other_profile,
        name="Other device",
        seed_envelope={"synthetic": "not-read"},
    )

    response = post_json(
        client,
        "/api/v1/admin/security/totp/confirmation",
        {"device_id": str(other_device.id), "token": "123456"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "verification_failed"


def test_recovery_confirmation_replaces_factors_and_promotes_current_session(db) -> None:
    password = "Synthetic-Recovery-Replace-934!"
    user = User.objects.create_user(username="synthetic.replace.owner", password=password)
    profile = AdministratorProfile.objects.create(user=user, status="active")
    old_enrollment = begin_totp_enrollment(profile, label="Old authenticator", now=timezone.now())
    old_enrollment.device.confirmed = True
    old_enrollment.device.confirmed_at = timezone.now()
    old_enrollment.device.save(update_fields=["confirmed", "confirmed_at"])
    old_codes = generate_recovery_batch(profile, now=timezone.now())
    client = Client()
    password_challenge(client, profile, password)
    post_json(
        client,
        "/api/v1/admin/auth/recovery",
        {"recovery_code": old_codes[0]},
    )
    current_before = AdministratorSession.objects.get(profile=profile, revoked_at__isnull=True)
    old_key = current_before.session_key
    extra_django = Session.objects.create(
        session_key="synthetic-recovery-extra",
        session_data="",
        expire_date=timezone.now() + timedelta(hours=1),
    )
    extra = AdministratorSession.objects.create(
        profile=profile,
        session_key=extra_django.session_key,
        authenticated_at=timezone.now(),
        last_activity_at=timezone.now(),
        absolute_expires_at=timezone.now() + timedelta(hours=1),
        expires_at=timezone.now() + timedelta(minutes=30),
        device_label="Extra browser",
        recovery_restricted=True,
    )
    enrollment = begin_enrollment(client).json()

    response = post_json(
        client,
        "/api/v1/admin/security/totp/confirmation",
        {
            "device_id": enrollment["device_id"],
            "token": token_for(enrollment["manual_secret"]),
        },
    )

    assert response.status_code == 200
    assert len(response.json()["recovery_codes"]) == 10
    profile.refresh_from_db()
    current_before.refresh_from_db()
    extra.refresh_from_db()
    old_enrollment.device.refresh_from_db()
    assert profile.status == AdministratorProfile.Status.ACTIVE
    assert current_before.recovery_restricted is False
    assert current_before.mfa_verified_at is not None
    assert current_before.session_key != old_key
    assert old_enrollment.device.disabled_at is not None
    assert extra.revoked_at is not None
    assert not Session.objects.filter(pk=extra_django.pk).exists()
    assert not RecoveryCode.objects.filter(
        profile=profile,
        invalidated_at__isnull=True,
        consumed_at__isnull=True,
        batch_id=RecoveryCode.objects.get(public_id=old_codes[0][:8]).batch_id,
    ).exists()


def test_fresh_reauthentication_rotates_session_and_expires_at_exact_boundary(
    enrollment_owner,
) -> None:
    profile, password = enrollment_owner
    client = Client()
    password_challenge(client, profile, password)
    enrollment = begin_enrollment(client).json()
    post_json(
        client,
        "/api/v1/admin/security/totp/confirmation",
        {
            "device_id": enrollment["device_id"],
            "token": token_for(enrollment["manual_secret"]),
        },
    )
    metadata = AdministratorSession.objects.get(profile=profile)
    old_key = metadata.session_key

    response = post_json(
        client,
        "/api/v1/admin/security/reauthentication",
        {
            "password": password,
            "token": token_for(enrollment["manual_secret"], offset_seconds=30),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"fresh": True}
    metadata.refresh_from_db()
    assert metadata.session_key != old_key
    assert metadata.fresh_verified_at is not None
    assert administrator_session_is_fresh(
        metadata,
        now=metadata.fresh_verified_at + timedelta(minutes=10) - timedelta(microseconds=1),
    )
    assert not administrator_session_is_fresh(
        metadata,
        now=metadata.fresh_verified_at + timedelta(minutes=10),
    )
    assert not administrator_session_is_fresh(
        metadata,
        now=metadata.fresh_verified_at - timedelta(microseconds=1),
    )


def test_failed_fresh_reauthentication_does_not_set_freshness(enrollment_owner) -> None:
    profile, password = enrollment_owner
    client = Client()
    password_challenge(client, profile, password)
    enrollment = begin_enrollment(client).json()
    post_json(
        client,
        "/api/v1/admin/security/totp/confirmation",
        {
            "device_id": enrollment["device_id"],
            "token": token_for(enrollment["manual_secret"]),
        },
    )

    response = post_json(
        client,
        "/api/v1/admin/security/reauthentication",
        {"password": "wrong-password", "token": "123456"},
    )

    assert response.status_code == 401
    metadata = AdministratorSession.objects.get(profile=profile)
    assert metadata.fresh_verified_at is None
