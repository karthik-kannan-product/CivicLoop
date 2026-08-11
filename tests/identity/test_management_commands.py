from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.management import CommandError, call_command
from django.utils import timezone
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
    AdministratorTOTPDevice,
    RecoveryCode,
)


@pytest.mark.django_db
def test_bootstrap_owner_creates_a_separate_unprivileged_owner() -> None:
    stdout = StringIO()
    with (
        patch("builtins.input", side_effect=["synthetic.owner", "owner@example.test"]),
        patch(
            "identity.management.commands.bootstrap_owner.getpass.getpass",
            side_effect=["Synthetic-Owner-Passphrase-934!", "Synthetic-Owner-Passphrase-934!"],
        ),
    ):
        call_command("bootstrap_owner", stdout=stdout)

    profile = AdministratorProfile.objects.select_related("user").get()
    assert profile.user.username == "synthetic.owner"
    assert profile.user.email == "owner@example.test"
    assert profile.user.check_password("Synthetic-Owner-Passphrase-934!")
    assert profile.user.is_staff is False
    assert profile.user.is_superuser is False
    assert not hasattr(profile.user, "launchloop_actor")
    assert profile.status == AdministratorProfile.Status.ENROLLMENT_REQUIRED
    assert AdministratorSecurityEvent.objects.filter(
        action="owner_bootstrapped",
        outcome="success",
        profile=profile,
    ).exists()
    assert "synthetic.owner" in stdout.getvalue()
    assert "Synthetic-Owner-Passphrase-934!" not in stdout.getvalue()


@pytest.mark.django_db
def test_bootstrap_owner_rejects_password_mismatch_without_writing_state() -> None:
    with (
        patch("builtins.input", side_effect=["synthetic.owner", "owner@example.test"]),
        patch(
            "identity.management.commands.bootstrap_owner.getpass.getpass",
            side_effect=["Synthetic-Owner-Passphrase-934!", "different-value"],
        ),
        pytest.raises(CommandError, match="Password confirmation does not match"),
    ):
        call_command("bootstrap_owner")

    assert not User.objects.exists()
    assert not AdministratorProfile.objects.exists()


@pytest.mark.django_db
def test_bootstrap_owner_applies_django_password_validation() -> None:
    with (
        patch("builtins.input", side_effect=["synthetic.owner", "owner@example.test"]),
        patch(
            "identity.management.commands.bootstrap_owner.getpass.getpass",
            side_effect=["short", "short"],
        ),
        pytest.raises(CommandError, match="password is too short"),
    ):
        call_command("bootstrap_owner")

    assert not AdministratorProfile.objects.exists()


@pytest.mark.django_db
def test_bootstrap_owner_refuses_when_an_enabled_owner_exists() -> None:
    user = User.objects.create_user(username="existing.owner")
    AdministratorProfile.objects.create(user=user, status=AdministratorProfile.Status.ACTIVE)

    with pytest.raises(CommandError, match="An enabled owner already exists"):
        call_command("bootstrap_owner")

    assert AdministratorProfile.objects.count() == 1


@pytest.fixture
def configured_owner(db) -> AdministratorProfile:
    user = User.objects.create_user(username="synthetic.reset.owner")
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    AdministratorTOTPDevice.objects.create(
        user=user,
        profile=profile,
        name="Synthetic device",
        confirmed=True,
        seed_envelope={"encrypted": "synthetic"},
    )
    RecoveryCode.objects.create(
        profile=profile,
        batch_id="ef7a99b9-6ab4-4968-b40a-9a722f579f13",
        public_id="AB12CD34",
        encoded_secret="pbkdf2_sha256$synthetic-hash-only",
    )
    Session.objects.create(
        session_key="synthetic-django-session",
        session_data="",
        expire_date=timezone.now() + timedelta(hours=1),
    )
    AdministratorSession.objects.create(
        profile=profile,
        session_key="synthetic-django-session",
        device_label="Synthetic Browser",
    )
    return profile


def test_reset_owner_mfa_cancellation_changes_nothing(
    configured_owner: AdministratorProfile,
) -> None:
    with (
        patch("builtins.input", return_value="wrong.owner"),
        pytest.raises(CommandError, match="Confirmation did not match"),
    ):
        call_command("reset_owner_mfa")

    configured_owner.refresh_from_db()
    assert configured_owner.status == AdministratorProfile.Status.ACTIVE
    assert AdministratorTOTPDevice.objects.get().disabled_at is None
    assert RecoveryCode.objects.get().invalidated_at is None
    assert AdministratorSession.objects.get().revoked_at is None
    assert Session.objects.filter(session_key="synthetic-django-session").exists()


def test_reset_owner_mfa_revokes_sessions_and_invalidates_credentials(
    configured_owner: AdministratorProfile,
) -> None:
    stdout = StringIO()
    with patch("builtins.input", return_value="synthetic.reset.owner"):
        call_command("reset_owner_mfa", stdout=stdout)

    configured_owner.refresh_from_db()
    assert configured_owner.status == AdministratorProfile.Status.ENROLLMENT_REQUIRED
    assert AdministratorTOTPDevice.objects.get().disabled_at is not None
    assert RecoveryCode.objects.get().invalidated_at is not None
    assert AdministratorSession.objects.get().revoked_at is not None
    assert not Session.objects.filter(session_key="synthetic-django-session").exists()
    assert AdministratorSecurityEvent.objects.filter(
        action="owner_mfa_reset",
        outcome="success",
        profile=configured_owner,
    ).exists()
    assert "synthetic.reset.owner" in stdout.getvalue()
