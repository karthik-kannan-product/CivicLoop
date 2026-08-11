from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from identity.exceptions import (
    IdentityCredentialMismatch,
    IdentityFreshnessRequired,
    IdentityValidationError,
)
from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
)
from identity.request_context import source_ip
from identity.services.credentials import generate_recovery_batch
from identity.services.security import (
    SecurityEventPage,
    list_security_events,
    record_security_event,
)
from identity.services.sessions import (
    administrator_session_is_fresh,
    revoke_session,
    rotate_administrator_session,
)


@dataclass(frozen=True)
class RecoveryRegeneration:
    recovery_codes: tuple[str, ...]
    revoked_count: int


def require_fresh_administrator(metadata: AdministratorSession) -> None:
    if metadata.recovery_restricted or not administrator_session_is_fresh(metadata):
        raise IdentityFreshnessRequired("Fresh administrator verification is required.")


def _revoke_other_sessions(
    profile: AdministratorProfile,
    current: AdministratorSession,
    *,
    now: datetime,
) -> int:
    others = AdministratorSession.objects.select_for_update().filter(
        profile=profile,
        revoked_at__isnull=True,
    ).exclude(pk=current.pk)
    session_keys = list(others.values_list("session_key", flat=True))
    count = others.update(revoked_at=now)
    Session.objects.filter(session_key__in=session_keys).delete()
    return count


@transaction.atomic
def change_password(
    request: HttpRequest,
    metadata: AdministratorSession,
    *,
    current_password: str,
    new_password: str,
) -> int:
    locked = AdministratorSession.objects.select_for_update().select_related(
        "profile__user"
    ).get(pk=metadata.pk)
    require_fresh_administrator(locked)
    user = locked.profile.user
    if not user.check_password(current_password):
        raise IdentityCredentialMismatch("The current password is invalid.")
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        raise IdentityValidationError(" ".join(exc.messages)) from None
    user.set_password(new_password)
    user.save(update_fields=["password"])
    now = timezone.now()
    revoked_count = _revoke_other_sessions(locked.profile, locked, now=now)
    update_session_auth_hash(request, user)
    locked.session_key = request.session.session_key
    locked.fresh_verified_at = None
    locked.save(update_fields=["session_key", "fresh_verified_at"])
    metadata.session_key = locked.session_key
    metadata.fresh_verified_at = None
    record_security_event(
        action="owner_password_changed",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=locked.profile,
        source_ip=source_ip(request),
        session_id=locked.id,
        details={"revoked_session_count": revoked_count},
    )
    return revoked_count


@transaction.atomic
def regenerate_recovery_codes(
    request: HttpRequest,
    metadata: AdministratorSession,
) -> RecoveryRegeneration:
    locked = AdministratorSession.objects.select_for_update().select_related("profile").get(
        pk=metadata.pk
    )
    require_fresh_administrator(locked)
    now = timezone.now()
    recovery_codes = tuple(generate_recovery_batch(locked.profile, now=now))
    revoked_count = _revoke_other_sessions(locked.profile, locked, now=now)
    rotate_administrator_session(request, locked)
    locked.fresh_verified_at = None
    locked.save(update_fields=["fresh_verified_at"])
    metadata.session_key = locked.session_key
    metadata.fresh_verified_at = None
    record_security_event(
        action="owner_recovery_codes_regenerated",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=locked.profile,
        source_ip=source_ip(request),
        session_id=locked.id,
        details={"revoked_session_count": revoked_count},
    )
    return RecoveryRegeneration(recovery_codes, revoked_count)


@transaction.atomic
def revoke_owned_session(
    request: HttpRequest,
    metadata: AdministratorSession,
    *,
    target_id,
) -> bool:
    revoked = revoke_session(
        metadata.profile,
        target_id,
        actor_session_id=metadata.id,
    )
    is_current = revoked.id == metadata.id
    if is_current:
        request.session.flush()
    return is_current


@transaction.atomic
def revoke_other_sessions(
    request: HttpRequest,
    metadata: AdministratorSession,
) -> int:
    locked = AdministratorSession.objects.select_for_update().select_related("profile").get(
        pk=metadata.pk
    )
    require_fresh_administrator(locked)
    now = timezone.now()
    revoked_count = _revoke_other_sessions(locked.profile, locked, now=now)
    locked.fresh_verified_at = None
    locked.save(update_fields=["fresh_verified_at"])
    metadata.fresh_verified_at = None
    record_security_event(
        action="owner_other_sessions_revoked",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=locked.profile,
        source_ip=source_ip(request),
        session_id=locked.id,
        details={"revoked_session_count": revoked_count},
    )
    return revoked_count


def sessions_for(profile: AdministratorProfile) -> tuple[AdministratorSession, ...]:
    return tuple(profile.administrator_sessions.order_by("-created_at", "-id"))


def events_for(
    profile: AdministratorProfile,
    *,
    cursor: str | None,
    limit: int,
) -> SecurityEventPage:
    return list_security_events(profile, cursor=cursor, limit=limit)
