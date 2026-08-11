import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from django_otp.oath import TOTP
from identity.exceptions import IdentityError
from identity.models import AdministratorProfile, AdministratorTOTPDevice
from identity.services.credentials import begin_totp_enrollment, verify_totp


@pytest.fixture
def owner_profile(db) -> AdministratorProfile:
    user = User.objects.create_user(username="synthetic.totp.owner")
    return AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
    )


@pytest.fixture
def identity_key_file(tmp_path: Path) -> Path:
    path = tmp_path / "identity-keyring.json"
    key = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
    path.write_text(
        json.dumps({"active_key_id": "identity-test", "keys": {"identity-test": key}}),
        encoding="utf-8",
    )
    return path


def decode_manual_secret(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def token_for(secret: str, when, *, drift: int = 0) -> str:
    generator = TOTP(decode_manual_secret(secret), 30, 0, 6, drift)
    generator.time = when.timestamp()
    return f"{generator.token():06d}"


def test_begin_totp_enrollment_returns_one_time_material_and_persists_only_envelope(
    owner_profile: AdministratorProfile,
    identity_key_file: Path,
) -> None:
    now = timezone.now()
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=identity_key_file):
        enrollment = begin_totp_enrollment(
            owner_profile,
            label="Synthetic authenticator",
            now=now,
        )

    device = AdministratorTOTPDevice.objects.get(id=enrollment.device.id)
    assert enrollment.manual_secret not in json.dumps(device.seed_envelope)
    assert enrollment.otpauth_uri.startswith("otpauth://totp/CivicLoop%3Asynthetic.totp.owner?")
    assert "issuer=CivicLoop" in enrollment.otpauth_uri
    assert "digits=6" in enrollment.otpauth_uri
    assert "period=30" in enrollment.otpauth_uri
    assert device.confirmed is False
    assert device.created_at is not None


def test_totp_verification_accepts_once_and_rejects_replay(
    owner_profile: AdministratorProfile,
    identity_key_file: Path,
) -> None:
    now = timezone.now().replace(microsecond=0)
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=identity_key_file):
        enrollment = begin_totp_enrollment(owner_profile, label="Synthetic", now=now)
        enrollment.device.confirmed = True
        enrollment.device.save(update_fields=["confirmed"])
        token = token_for(enrollment.manual_secret, now)
        verified = verify_totp(enrollment.device, token, now=now)
        with pytest.raises(IdentityError, match="Verification failed"):
            verify_totp(enrollment.device, token, now=now)

    assert verified.last_t == int(now.timestamp()) // 30
    assert verified.last_used_at == now
    assert verified.throttling_failure_count == 0


def test_totp_verification_accepts_one_adjacent_step_and_updates_drift(
    owner_profile: AdministratorProfile,
    identity_key_file: Path,
) -> None:
    now = timezone.now().replace(microsecond=0)
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=identity_key_file):
        enrollment = begin_totp_enrollment(owner_profile, label="Synthetic", now=now)
        enrollment.device.confirmed = True
        enrollment.device.save(update_fields=["confirmed"])
        verified = verify_totp(
            enrollment.device,
            token_for(enrollment.manual_secret, now, drift=1),
            now=now,
        )

    assert verified.drift == 1


def test_invalid_totp_increments_persistent_exponential_throttle(
    owner_profile: AdministratorProfile,
    identity_key_file: Path,
) -> None:
    now = timezone.now().replace(microsecond=0)
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=identity_key_file):
        enrollment = begin_totp_enrollment(owner_profile, label="Synthetic", now=now)
        enrollment.device.confirmed = True
        enrollment.device.save(update_fields=["confirmed"])
        with pytest.raises(IdentityError, match="Verification failed"):
            verify_totp(enrollment.device, "000000", now=now)
        with pytest.raises(IdentityError, match="Verification failed"):
            verify_totp(
                enrollment.device,
                token_for(enrollment.manual_secret, now),
                now=now + timedelta(milliseconds=500),
            )

    enrollment.device.refresh_from_db()
    assert enrollment.device.throttling_failure_count == 1
    assert enrollment.device.throttling_failure_timestamp == now


@pytest.mark.parametrize("token", ["", "123", "abcdef", "1234567"])
def test_totp_rejects_malformed_tokens_generically(
    owner_profile: AdministratorProfile,
    identity_key_file: Path,
    token: str,
) -> None:
    now = timezone.now().replace(microsecond=0)
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=identity_key_file):
        enrollment = begin_totp_enrollment(owner_profile, label="Synthetic", now=now)
        enrollment.device.confirmed = True
        enrollment.device.save(update_fields=["confirmed"])
        with pytest.raises(IdentityError, match="Verification failed"):
            verify_totp(enrollment.device, token, now=now)


def test_unconfirmed_or_disabled_totp_device_cannot_authenticate(
    owner_profile: AdministratorProfile,
    identity_key_file: Path,
) -> None:
    now = timezone.now().replace(microsecond=0)
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=identity_key_file):
        enrollment = begin_totp_enrollment(owner_profile, label="Synthetic", now=now)
        token = token_for(enrollment.manual_secret, now)
        with pytest.raises(IdentityError, match="Verification failed"):
            verify_totp(enrollment.device, token, now=now)
        enrollment.device.confirmed = True
        enrollment.device.disabled_at = now
        enrollment.device.save(update_fields=["confirmed", "disabled_at"])
        with pytest.raises(IdentityError, match="Verification failed"):
            verify_totp(enrollment.device, token, now=now)
