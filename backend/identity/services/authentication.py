from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth import authenticate
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from identity.models import AdministratorProfile, AdministratorSecurityEvent
from identity.rate_limits import check_password_limit, record_limit_success
from identity.request_context import source_ip
from identity.services.security import record_security_event

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
