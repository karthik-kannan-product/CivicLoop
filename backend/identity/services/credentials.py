from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from identity.exceptions import IdentityError
from identity.models import (
    AdministratorProfile,
    AdministratorSession,
    AdministratorTOTPDevice,
    RecoveryCode,
)
from identity.services.security import record_security_event


@transaction.atomic
def bootstrap_owner(
    *,
    username: str,
    email: str,
    password: str,
) -> AdministratorProfile:
    if AdministratorProfile.objects.exclude(
        status=AdministratorProfile.Status.DISABLED
    ).exists():
        raise IdentityError("An enabled owner already exists.")

    user = User(
        username=User.normalize_username(username.strip()),
        email=User.objects.normalize_email(email.strip()),
        is_staff=False,
        is_superuser=False,
        is_active=True,
    )
    try:
        user.full_clean(exclude=("password",))
        validate_password(password, user=user)
    except ValidationError as error:
        raise IdentityError(" ".join(error.messages)) from None
    user.set_password(password)
    try:
        user.save()
        profile = AdministratorProfile.objects.create(
            user=user,
            status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
        )
    except IntegrityError:
        raise IdentityError("An enabled owner already exists.") from None
    record_security_event(
        action="owner_bootstrapped",
        outcome="success",
        owner=profile,
        source_ip=None,
        session_id=None,
        target_type="administrator_profile",
        target_id=str(profile.id),
        details={"method": "interactive_management_command"},
    )
    return profile


@transaction.atomic
def reset_owner_mfa(
    *,
    profile: AdministratorProfile,
    confirmed_username: str,
) -> None:
    if confirmed_username != profile.user.username:
        raise IdentityError("Confirmation did not match the owner username.")

    now = timezone.now()
    session_keys = list(
        AdministratorSession.objects.select_for_update()
        .filter(profile=profile, revoked_at__isnull=True)
        .values_list("session_key", flat=True)
    )
    revoked_count = AdministratorSession.objects.filter(
        profile=profile,
        revoked_at__isnull=True,
    ).update(revoked_at=now)
    if session_keys:
        Session.objects.filter(session_key__in=session_keys).delete()
    disabled_device_count = AdministratorTOTPDevice.objects.filter(
        profile=profile,
        disabled_at__isnull=True,
    ).update(disabled_at=now, confirmed=False)
    invalidated_code_count = RecoveryCode.objects.filter(
        profile=profile,
        invalidated_at__isnull=True,
    ).update(invalidated_at=now)
    profile.status = AdministratorProfile.Status.ENROLLMENT_REQUIRED
    profile.save(update_fields=("status", "updated_at"))
    record_security_event(
        action="owner_mfa_reset",
        outcome="success",
        owner=profile,
        source_ip=None,
        session_id=None,
        target_type="administrator_profile",
        target_id=str(profile.id),
        details={
            "method": "interactive_management_command",
            "revoked_session_count": revoked_count,
            "disabled_device_count": disabled_device_count,
            "invalidated_code_count": invalidated_code_count,
        },
    )
