from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django_otp.models import Device, ThrottlingMixin
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
    AdministratorTOTPDevice,
    RecoveryCode,
)


@pytest.fixture
def owner_user(db) -> User:
    return User.objects.create_user(username="synthetic.owner", password="not-a-real-password")


@pytest.fixture
def owner_profile(owner_user: User) -> AdministratorProfile:
    return AdministratorProfile.objects.create(
        user=owner_user,
        status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
    )


def test_administrator_profile_is_uuid_backed_and_separate_from_demo_roles(
    owner_profile: AdministratorProfile,
) -> None:
    assert owner_profile.id.version == 4
    assert owner_profile.user.is_staff is False
    assert owner_profile.user.is_superuser is False
    assert not hasattr(owner_profile.user, "launchloop_actor")


@pytest.mark.django_db(transaction=True)
def test_only_one_non_disabled_owner_profile_is_allowed() -> None:
    first_user = User.objects.create_user(username="synthetic.owner.one")
    AdministratorProfile.objects.create(
        user=first_user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    second_user = User.objects.create_user(username="synthetic.owner.two")

    with pytest.raises(IntegrityError), transaction.atomic():
        AdministratorProfile.objects.create(
            user=second_user,
            status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
        )

    disabled_user = User.objects.create_user(username="synthetic.owner.disabled")
    disabled = AdministratorProfile.objects.create(
        user=disabled_user,
        status=AdministratorProfile.Status.DISABLED,
    )
    assert disabled.status == AdministratorProfile.Status.DISABLED


def test_totp_device_uses_django_otp_interfaces_without_plaintext_key_storage(
    owner_profile: AdministratorProfile,
) -> None:
    device = AdministratorTOTPDevice.objects.create(
        id=uuid4(),
        user=owner_profile.user,
        profile=owner_profile,
        name="Synthetic authenticator",
        confirmed=False,
        seed_envelope={"encrypted": "synthetic"},
    )

    assert isinstance(device, Device)
    assert isinstance(device, ThrottlingMixin)
    assert device.digits == 6
    assert device.step == 30
    assert device.last_t == -1
    field_names = {field.name for field in AdministratorTOTPDevice._meta.get_fields()}
    assert "key" not in field_names
    assert "seed" not in field_names
    assert "seed_envelope" in field_names


def test_recovery_codes_store_only_lookup_and_encoded_secret(
    owner_profile: AdministratorProfile,
) -> None:
    code = RecoveryCode.objects.create(
        profile=owner_profile,
        batch_id=uuid4(),
        public_id="AB12CD34",
        encoded_secret="pbkdf2_sha256$synthetic-hash-only",
    )

    assert code.public_id == "AB12CD34"
    field_names = {field.name for field in RecoveryCode._meta.get_fields()}
    assert "secret" not in field_names
    assert "plaintext" not in field_names


def test_administrator_session_keeps_internal_session_key_separate(
    owner_profile: AdministratorProfile,
) -> None:
    session = AdministratorSession.objects.create(
        profile=owner_profile,
        session_key="synthetic-internal-session-key",
        device_label="Synthetic Browser on Test OS",
        user_agent="SyntheticBrowser/1.0",
    )

    assert session.id.version == 4
    assert session.session_key != str(session.id)
    assert session.recovery_restricted is False


def test_security_event_has_only_bounded_identity_audit_fields(
    owner_profile: AdministratorProfile,
) -> None:
    event = AdministratorSecurityEvent.objects.create(
        profile=owner_profile,
        user=owner_profile.user,
        action="owner_bootstrapped",
        outcome="success",
        target_type="administrator_profile",
        target_id=str(owner_profile.id),
        details={"source": "synthetic_test"},
        source_ip="127.0.0.1",
    )

    assert event.id.version == 4
    assert event.details == {"source": "synthetic_test"}
    assert event.created_at is not None
