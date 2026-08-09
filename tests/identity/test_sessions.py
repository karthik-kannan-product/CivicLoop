from datetime import timedelta
from io import StringIO
from uuid import uuid4

import pytest
from django.contrib.auth import login as django_login
from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone
from identity.models import AdministratorProfile, AdministratorSession
from identity.services.sessions import (
    ADMIN_SESSION_KEY,
    enforce_administrator_session,
    establish_administrator_session,
    revoke_session,
    rotate_administrator_session,
)

ADMIN_ENABLED = override_settings(CIVICLOOP_ADMIN_IDENTITY_ENABLED=True)


@pytest.fixture
def owner_profile(db) -> AdministratorProfile:
    user = User.objects.create_user(username="synthetic.session.owner")
    return AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )


def authenticated_request(user: User, *, user_agent: str = "Synthetic Browser/1.0"):
    request = RequestFactory().get(
        "/api/v1/admin/security/status",
        HTTP_USER_AGENT=user_agent,
        REMOTE_ADDR="192.0.2.25",
    )
    SessionMiddleware(lambda _request: None).process_request(request)
    request.session.save()
    django_login(request, user)
    request.user = user
    return request


@ADMIN_ENABLED
def test_establishes_bounded_database_backed_administrator_session(
    owner_profile: AdministratorProfile,
) -> None:
    request = authenticated_request(owner_profile.user, user_agent="X" * 1000)
    verified_at = timezone.now()

    metadata = establish_administrator_session(
        request,
        owner_profile,
        mfa_verified_at=verified_at,
        recovery_restricted=False,
    )

    assert request.session[ADMIN_SESSION_KEY] == str(metadata.id)
    assert metadata.session_key == request.session.session_key
    assert metadata.authenticated_at == verified_at
    assert metadata.mfa_verified_at == verified_at
    assert metadata.last_activity_at == verified_at
    assert metadata.expires_at == verified_at + timedelta(minutes=30)
    assert metadata.absolute_expires_at == verified_at + timedelta(hours=12)
    assert metadata.source_ip == "192.0.2.25"
    assert len(metadata.device_label) <= 160
    assert len(metadata.user_agent) <= 512
    assert Session.objects.filter(session_key=metadata.session_key).exists()


@ADMIN_ENABLED
def test_rotation_preserves_public_metadata_and_replaces_internal_key(
    owner_profile: AdministratorProfile,
) -> None:
    request = authenticated_request(owner_profile.user)
    metadata = establish_administrator_session(
        request,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )
    old_key = metadata.session_key

    rotate_administrator_session(request, metadata)

    metadata.refresh_from_db()
    assert metadata.session_key == request.session.session_key
    assert metadata.session_key != old_key
    assert not Session.objects.filter(session_key=old_key).exists()


@ADMIN_ENABLED
def test_enforcement_extends_idle_expiry_but_never_absolute_expiry(
    owner_profile: AdministratorProfile,
) -> None:
    request = authenticated_request(owner_profile.user)
    metadata = establish_administrator_session(
        request,
        owner_profile,
        mfa_verified_at=timezone.now() - timedelta(hours=11, minutes=31),
        recovery_restricted=False,
    )
    now = timezone.now()
    metadata.last_activity_at = now - timedelta(minutes=2)
    metadata.expires_at = now + timedelta(minutes=1)
    metadata.absolute_expires_at = now + timedelta(minutes=10)
    metadata.save()

    enforced = enforce_administrator_session(request, now=now)

    assert enforced is not None
    enforced.refresh_from_db()
    assert enforced.expires_at == enforced.absolute_expires_at
    assert enforced.last_activity_at == now


@pytest.mark.parametrize("expiry_field", ["expires_at", "absolute_expires_at"])
@ADMIN_ENABLED
def test_expired_session_is_revoked_and_django_session_removed(
    owner_profile: AdministratorProfile,
    expiry_field: str,
) -> None:
    request = authenticated_request(owner_profile.user)
    metadata = establish_administrator_session(
        request,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )
    session_key = metadata.session_key
    setattr(metadata, expiry_field, timezone.now() - timedelta(seconds=1))
    metadata.save(update_fields=[expiry_field])

    assert enforce_administrator_session(request) is None

    metadata.refresh_from_db()
    assert metadata.revoked_at is not None
    assert not Session.objects.filter(session_key=session_key).exists()


@ADMIN_ENABLED
def test_disabled_owner_session_is_denied(owner_profile: AdministratorProfile) -> None:
    request = authenticated_request(owner_profile.user)
    metadata = establish_administrator_session(
        request,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )
    owner_profile.status = AdministratorProfile.Status.DISABLED
    owner_profile.save(update_fields=["status", "updated_at"])

    assert enforce_administrator_session(request) is None

    metadata.refresh_from_db()
    assert metadata.revoked_at is not None


@ADMIN_ENABLED
def test_missing_or_mismatched_metadata_fails_closed(owner_profile: AdministratorProfile) -> None:
    missing = authenticated_request(owner_profile.user)
    missing.session[ADMIN_SESSION_KEY] = str(uuid4())
    missing.session.save()
    assert enforce_administrator_session(missing) is None

    mismatched = authenticated_request(owner_profile.user)
    metadata = establish_administrator_session(
        mismatched,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )
    mismatched.session.cycle_key()
    assert enforce_administrator_session(mismatched) is None
    metadata.refresh_from_db()
    assert metadata.revoked_at is not None


@ADMIN_ENABLED
def test_demo_session_is_ignored_without_mutation(db) -> None:
    demo = User.objects.create_user(username="synthetic.demo.session")
    request = authenticated_request(demo)
    original_key = request.session.session_key

    assert enforce_administrator_session(request) is None
    assert request.session.session_key == original_key
    assert request.user.is_authenticated


@ADMIN_ENABLED
def test_recovery_restricted_session_uses_explicit_route_allowlist(
    owner_profile: AdministratorProfile,
) -> None:
    client = Client()
    client.force_login(owner_profile.user)
    session = client.session
    now = timezone.now()
    metadata = AdministratorSession.objects.create(
        profile=owner_profile,
        session_key=session.session_key,
        authenticated_at=now,
        last_activity_at=now,
        mfa_verified_at=now,
        absolute_expires_at=now + timedelta(hours=12),
        expires_at=now + timedelta(minutes=30),
        device_label="Synthetic Browser",
        recovery_restricted=True,
    )
    session[ADMIN_SESSION_KEY] = str(metadata.id)
    session.save()

    denied = client.get("/api/v1/demo")
    allowed = client.get("/api/v1/admin/security/status")

    assert denied.status_code == 403
    assert denied.json()["code"] == "recovery_restricted"
    assert denied["Cache-Control"] == "no-store"
    assert allowed.status_code == 200


@ADMIN_ENABLED
def test_owned_session_revocation_removes_django_session(
    owner_profile: AdministratorProfile,
) -> None:
    request = authenticated_request(owner_profile.user)
    metadata = establish_administrator_session(
        request,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )

    revoked = revoke_session(
        owner_profile,
        metadata.id,
        actor_session_id=metadata.id,
    )

    assert revoked.id == metadata.id
    assert revoked.revoked_at is not None
    assert not Session.objects.filter(session_key=metadata.session_key).exists()


@ADMIN_ENABLED
def test_unknown_owned_session_cannot_be_enumerated(owner_profile: AdministratorProfile) -> None:
    with pytest.raises(AdministratorSession.DoesNotExist):
        revoke_session(owner_profile, uuid4(), actor_session_id=None)


@ADMIN_ENABLED
def test_cleanup_is_idempotent_and_preserves_active_sessions(
    owner_profile: AdministratorProfile,
) -> None:
    active_request = authenticated_request(owner_profile.user)
    active = establish_administrator_session(
        active_request,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )
    expired_request = authenticated_request(owner_profile.user)
    expired = establish_administrator_session(
        expired_request,
        owner_profile,
        mfa_verified_at=timezone.now(),
        recovery_restricted=False,
    )
    expired.expires_at = timezone.now() - timedelta(seconds=1)
    expired.save(update_fields=["expires_at"])
    stdout = StringIO()

    call_command("purge_administrator_sessions", stdout=stdout)
    call_command("purge_administrator_sessions", stdout=stdout)

    active.refresh_from_db()
    expired.refresh_from_db()
    assert active.revoked_at is None
    assert Session.objects.filter(session_key=active.session_key).exists()
    assert expired.revoked_at is not None
    assert not Session.objects.filter(session_key=expired.session_key).exists()
