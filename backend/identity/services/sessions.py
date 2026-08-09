from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from identity.models import (
    AdministratorProfile,
    AdministratorSecurityEvent,
    AdministratorSession,
)
from identity.request_context import source_ip
from identity.services.security import record_security_event

ADMIN_SESSION_KEY = "civicloop_admin_session_id"


def _browser_metadata(request: HttpRequest) -> tuple[str, str]:
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    bounded_agent = user_agent[:512] if isinstance(user_agent, str) else ""
    label = bounded_agent[:160].strip() or "Unknown browser"
    return label, bounded_agent


@transaction.atomic
def establish_administrator_session(
    request: HttpRequest,
    profile: AdministratorProfile,
    *,
    mfa_verified_at: datetime | None,
    recovery_restricted: bool,
) -> AdministratorSession:
    if request.session.session_key is None:
        request.session.save()
    label, user_agent = _browser_metadata(request)
    authenticated_at = mfa_verified_at or timezone.now()
    absolute_expiry = authenticated_at + timedelta(seconds=settings.ADMIN_ABSOLUTE_SECONDS)
    idle_expiry = min(
        authenticated_at + timedelta(seconds=settings.ADMIN_IDLE_SECONDS),
        absolute_expiry,
    )
    metadata = AdministratorSession.objects.create(
        profile=profile,
        session_key=request.session.session_key,
        authenticated_at=authenticated_at,
        last_activity_at=authenticated_at,
        mfa_verified_at=mfa_verified_at,
        fresh_verified_at=None,
        absolute_expires_at=absolute_expiry,
        expires_at=idle_expiry,
        device_label=label,
        user_agent=user_agent,
        source_ip=source_ip(request),
        recovery_restricted=recovery_restricted,
    )
    request.session[ADMIN_SESSION_KEY] = str(metadata.id)
    request.session.set_expiry(0)
    request.session.save()
    return metadata


@transaction.atomic
def rotate_administrator_session(
    request: HttpRequest,
    metadata: AdministratorSession,
) -> None:
    locked = AdministratorSession.objects.select_for_update().get(pk=metadata.pk)
    request.session.cycle_key()
    request.session[ADMIN_SESSION_KEY] = str(locked.id)
    request.session.set_expiry(0)
    request.session.save()
    locked.session_key = request.session.session_key
    locked.save(update_fields=["session_key"])
    metadata.session_key = locked.session_key


def _invalidate_request_session(
    request: HttpRequest,
    metadata: AdministratorSession | None,
    *,
    now: datetime,
) -> None:
    if metadata is not None and metadata.revoked_at is None:
        AdministratorSession.objects.filter(pk=metadata.pk, revoked_at__isnull=True).update(
            revoked_at=now
        )
    django_logout(request)


def enforce_administrator_session(
    request: HttpRequest,
    *,
    now: datetime | None = None,
) -> AdministratorSession | None:
    if not settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED:
        return None
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    try:
        profile = AdministratorProfile.objects.get(user_id=user.pk)
    except AdministratorProfile.DoesNotExist:
        return None

    checked_at = now or timezone.now()
    public_id = request.session.get(ADMIN_SESSION_KEY)
    try:
        metadata_id = UUID(public_id) if isinstance(public_id, str) else None
    except ValueError:
        metadata_id = None
    metadata = None
    if metadata_id is not None:
        metadata = (
            AdministratorSession.objects.select_related("profile")
            .filter(pk=metadata_id, profile=profile)
            .first()
        )
    invalid = (
        metadata is None
        or metadata.session_key != request.session.session_key
        or metadata.revoked_at is not None
        or metadata.expires_at is None
        or metadata.expires_at <= checked_at
        or metadata.absolute_expires_at is None
        or metadata.absolute_expires_at <= checked_at
        or profile.status == AdministratorProfile.Status.DISABLED
    )
    if invalid:
        _invalidate_request_session(request, metadata, now=checked_at)
        return None

    update_after = timedelta(seconds=settings.ADMIN_ACTIVITY_UPDATE_SECONDS)
    if (
        metadata.last_activity_at is None
        or checked_at - metadata.last_activity_at >= update_after
    ):
        metadata.last_activity_at = checked_at
        metadata.expires_at = min(
            checked_at + timedelta(seconds=settings.ADMIN_IDLE_SECONDS),
            metadata.absolute_expires_at,
        )
        metadata.source_ip = source_ip(request)
        metadata.save(update_fields=["last_activity_at", "expires_at", "source_ip"])
    return metadata


@transaction.atomic
def revoke_session(
    profile: AdministratorProfile,
    public_id: UUID,
    *,
    actor_session_id: UUID | None,
) -> AdministratorSession:
    metadata = AdministratorSession.objects.select_for_update().get(
        pk=public_id,
        profile=profile,
    )
    if metadata.revoked_at is None:
        metadata.revoked_at = timezone.now()
        metadata.save(update_fields=["revoked_at"])
    Session.objects.filter(session_key=metadata.session_key).delete()
    record_security_event(
        action="owner_session_revoked",
        outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        owner=profile,
        source_ip=None,
        session_id=actor_session_id,
        target_type="administrator_session",
        target_id=str(metadata.id),
    )
    return metadata
