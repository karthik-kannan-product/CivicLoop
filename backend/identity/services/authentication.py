from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from identity.exceptions import IdentityError
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
    AdministratorTOTPDevice,
)
from identity.rate_limits import (
    check_password_limit,
    check_recovery_limit,
    record_limit_success,
)
from identity.request_context import source_ip
from identity.services.credentials import consume_recovery_code, verify_totp
from identity.services.security import record_security_event
from identity.services.sessions import establish_administrator_session

PREAUTH_SESSION_KEY = "civicloop_admin_preauth"
PREAUTH_STAGE = "password_verified"


@dataclass(frozen=True)
class PasswordChallenge:
    profile: AdministratorProfile
    next_action: str


def _eligible_profile(user: object) -> AdministratorProfile | None:
    if user is None:
        return None
    try:
        profile = user.administrator_profile
    except AdministratorProfile.DoesNotExist:
        return None
    if profile.status == AdministratorProfile.Status.DISABLED:
        return None
    return profile


def verify_owner_password(
    request: HttpRequest,
    *,
    username: str,
    password: str,
) -> PasswordChallenge | None:
    normalized_username = username.strip()
    client_ip = source_ip(request)
    scope = check_password_limit(
        normalized_owner=normalized_username,
        source_ip=client_ip,
    )
    user = authenticate(request, username=normalized_username, password=password)
    profile = _eligible_profile(user)
    if profile is None:
        record_security_event(
            action="owner_password_verified",
            outcome=AdministratorSecurityEvent.Outcome.FAILURE,
            owner=None,
            source_ip=client_ip,
            session_id=None,
        )
        return None

    record_limit_success(scope)
    now = timezone.now()
    request.session.flush()
    request.session[PREAUTH_SESSION_KEY] = {
        "owner_id": str(profile.id),
        "stage": PREAUTH_STAGE,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=settings.ADMIN_PREAUTH_SECONDS)).isoformat(),
        "correlation_id": str(uuid4()),
    }
    request.session.save()
    record_security_event(
        action="owner_password_verified",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=profile,
        source_ip=client_ip,
        session_id=None,
    )
    next_action = (
        "verify_totp"
        if profile.status == AdministratorProfile.Status.ACTIVE
        else "enroll_totp"
    )
    return PasswordChallenge(profile=profile, next_action=next_action)


def valid_preauthentication(request: HttpRequest) -> dict[str, str] | None:
    value = request.session.get(PREAUTH_SESSION_KEY)
    if not isinstance(value, dict) or set(value) != {
        "owner_id",
        "stage",
        "issued_at",
        "expires_at",
        "correlation_id",
    }:
        request.session.pop(PREAUTH_SESSION_KEY, None)
        return None
    if not all(isinstance(item, str) for item in value.values()):
        request.session.pop(PREAUTH_SESSION_KEY, None)
        return None
    try:
        UUID(value["owner_id"])
        UUID(value["correlation_id"])
        issued_at = parse_datetime(value["issued_at"])
        expires_at = parse_datetime(value["expires_at"])
    except (TypeError, ValueError):
        issued_at = expires_at = None
    now = timezone.now()
    if (
        value["stage"] != PREAUTH_STAGE
        or issued_at is None
        or expires_at is None
        or issued_at.tzinfo is None
        or expires_at.tzinfo is None
        or issued_at > now
        or expires_at <= now
        or expires_at - issued_at > timedelta(seconds=settings.ADMIN_PREAUTH_SECONDS)
    ):
        request.session.pop(PREAUTH_SESSION_KEY, None)
        return None
    return value


def preauthenticated_profile(request: HttpRequest) -> AdministratorProfile | None:
    state = valid_preauthentication(request)
    if state is None:
        return None
    profile = (
        AdministratorProfile.objects.select_related("user")
        .filter(pk=state["owner_id"])
        .exclude(status=AdministratorProfile.Status.DISABLED)
        .first()
    )
    if profile is None:
        request.session.pop(PREAUTH_SESSION_KEY, None)
    return profile


@transaction.atomic
def _establish_totp_session(
    request: HttpRequest,
    profile: AdministratorProfile,
    *,
    verified_at: datetime,
) -> AdministratorSession:
    django_login(
        request,
        profile.user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    request.session.pop(PREAUTH_SESSION_KEY, None)
    metadata = establish_administrator_session(
        request,
        profile,
        mfa_verified_at=verified_at,
        recovery_restricted=False,
    )
    record_security_event(
        action="owner_totp_verified",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=profile,
        source_ip=source_ip(request),
        session_id=metadata.id,
    )
    return metadata


def complete_totp_authentication(
    request: HttpRequest,
    profile: AdministratorProfile,
    *,
    token: str,
) -> AdministratorSession:
    if profile.status != AdministratorProfile.Status.ACTIVE:
        raise IdentityError("Verification failed.")
    device = (
        AdministratorTOTPDevice.objects.filter(
            profile=profile,
            confirmed=True,
            disabled_at__isnull=True,
            replaced_at__isnull=True,
        )
        .order_by("-confirmed_at", "-created_at")
        .first()
    )
    now = timezone.now()
    if device is None:
        record_security_event(
            action="owner_totp_verified",
            outcome=AdministratorSecurityEvent.Outcome.FAILURE,
            owner=profile,
            source_ip=source_ip(request),
            session_id=None,
        )
        raise IdentityError("Verification failed.")
    try:
        verify_totp(device, token, now=now)
    except IdentityError:
        record_security_event(
            action="owner_totp_verified",
            outcome=AdministratorSecurityEvent.Outcome.FAILURE,
            owner=profile,
            source_ip=source_ip(request),
            session_id=None,
        )
        raise
    return _establish_totp_session(request, profile, verified_at=now)


@transaction.atomic
def _complete_recovery_authentication_transaction(
    request: HttpRequest,
    profile: AdministratorProfile,
    *,
    recovery_code: str,
) -> AdministratorSession | None:
    if profile.status != AdministratorProfile.Status.ACTIVE:
        raise IdentityError("Verification failed.")
    client_ip = source_ip(request)
    scope = check_recovery_limit(owner_id=profile.id, source_ip=client_ip)
    now = timezone.now()
    try:
        consume_recovery_code(profile, recovery_code, now=now)
    except IdentityError:
        record_security_event(
            action="owner_recovery_verified",
            outcome=AdministratorSecurityEvent.Outcome.FAILURE,
            owner=profile,
            source_ip=client_ip,
            session_id=None,
        )
        return None
    record_limit_success(scope)
    other_sessions = AdministratorSession.objects.select_for_update().filter(
        profile=profile,
        revoked_at__isnull=True,
    )
    other_keys = list(other_sessions.values_list("session_key", flat=True))
    other_sessions.update(revoked_at=now)
    Session.objects.filter(session_key__in=other_keys).delete()
    profile.status = AdministratorProfile.Status.RECOVERY_REQUIRED
    profile.save(update_fields=["status", "updated_at"])
    django_login(
        request,
        profile.user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    request.session.pop(PREAUTH_SESSION_KEY, None)
    metadata = establish_administrator_session(
        request,
        profile,
        mfa_verified_at=None,
        recovery_restricted=True,
    )
    record_security_event(
        action="owner_recovery_verified",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=profile,
        source_ip=client_ip,
        session_id=metadata.id,
        details={"revoked_session_count": len(other_keys)},
    )
    return metadata


def complete_recovery_authentication(
    request: HttpRequest,
    profile: AdministratorProfile,
    *,
    recovery_code: str,
) -> AdministratorSession:
    metadata = _complete_recovery_authentication_transaction(
        request,
        profile,
        recovery_code=recovery_code,
    )
    if metadata is None:
        raise IdentityError("Verification failed.")
    return metadata


@transaction.atomic
def logout_administrator(request: HttpRequest) -> bool:
    metadata = getattr(request, "administrator_session", None)
    if not isinstance(metadata, AdministratorSession):
        if PREAUTH_SESSION_KEY in request.session:
            request.session.flush()
            return True
        return False
    now = timezone.now()
    locked = AdministratorSession.objects.select_for_update().get(pk=metadata.pk)
    if locked.revoked_at is None:
        locked.revoked_at = now
        locked.save(update_fields=["revoked_at"])
    record_security_event(
        action="owner_logout",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=locked.profile,
        source_ip=source_ip(request),
        session_id=locked.id,
    )
    django_logout(request)
    return True
