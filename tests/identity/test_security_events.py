from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from identity.exceptions import IdentityError
from identity.models import AdministratorProfile, AdministratorSecurityEvent
from identity.services.security import list_security_events, record_security_event


@pytest.fixture
def owner_profile(db) -> AdministratorProfile:
    user = User.objects.create_user(username="synthetic.security.owner")
    return AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )


def test_records_bounded_allowlisted_security_details(
    owner_profile: AdministratorProfile,
) -> None:
    event = record_security_event(
        action="owner_login",
        outcome="success",
        owner=owner_profile,
        source_ip="127.0.0.1",
        session_id=None,
        target_type="administrator_profile",
        target_id=str(owner_profile.id),
        details={
            "authentication_stage": "totp",
            "attempt_count": 1,
            "flags": ["synthetic", True],
        },
    )

    assert event.details == {
        "authentication_stage": "totp",
        "attempt_count": 1,
        "flags": ["synthetic", True],
    }
    assert event.user == owner_profile.user


@pytest.mark.parametrize(
    "details",
    [
        {"password": "synthetic"},
        {"nested": {"totp_token": "000000"}},
        {"items": [{"recovery_code": "synthetic"}]},
        {"cookie": "synthetic"},
        {"session_key": "synthetic"},
        {"authorization": "synthetic"},
        {"key_id": "synthetic"},
        {"user_agent": "SyntheticBrowser/1.0"},
    ],
)
def test_rejects_sensitive_detail_keys_recursively(
    owner_profile: AdministratorProfile,
    details: dict[str, object],
) -> None:
    with pytest.raises(IdentityError, match="Security event details are invalid"):
        record_security_event(
            action="owner_login",
            outcome="failure",
            owner=owner_profile,
            source_ip=None,
            session_id=None,
            details=details,
        )

    assert not AdministratorSecurityEvent.objects.exists()


@pytest.mark.parametrize(
    "details",
    [
        {"message": "x" * 257},
        {"items": list(range(51))},
        {"a": {"b": {"c": {"d": "too deep"}}}},
        {"unsupported": object()},
    ],
)
def test_rejects_oversized_or_unsupported_details(
    owner_profile: AdministratorProfile,
    details: dict[str, object],
) -> None:
    with pytest.raises(IdentityError, match="Security event details are invalid"):
        record_security_event(
            action="owner_login",
            outcome="failure",
            owner=owner_profile,
            source_ip=None,
            session_id=None,
            details=details,
        )


def test_audit_failure_rolls_back_the_protected_change(
    owner_profile: AdministratorProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create(**kwargs):
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(AdministratorSecurityEvent.objects, "create", fail_create)

    with pytest.raises(RuntimeError, match="synthetic database failure"):
        with transaction.atomic():
            owner_profile.status = AdministratorProfile.Status.RECOVERY_REQUIRED
            owner_profile.save(update_fields=["status", "updated_at"])
            record_security_event(
                action="owner_recovery_started",
                outcome="success",
                owner=owner_profile,
                source_ip=None,
                session_id=None,
            )

    owner_profile.refresh_from_db()
    assert owner_profile.status == AdministratorProfile.Status.ACTIVE


def test_lists_security_events_with_stable_signed_cursor_pagination(
    owner_profile: AdministratorProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_time = timezone.now()
    timestamps = iter([base_time + timedelta(seconds=index) for index in range(3)])
    monkeypatch.setattr("django.db.models.fields.timezone.now", lambda: next(timestamps))
    for index in range(3):
        AdministratorSecurityEvent.objects.create(
            profile=owner_profile,
            user=owner_profile.user,
            action=f"synthetic_event_{index}",
            outcome=AdministratorSecurityEvent.Outcome.SUCCESS,
        )

    first_page = list_security_events(owner_profile, cursor=None, limit=2)
    second_page = list_security_events(owner_profile, cursor=first_page.next_cursor, limit=2)

    assert [event.action for event in first_page.events] == [
        "synthetic_event_2",
        "synthetic_event_1",
    ]
    assert [event.action for event in second_page.events] == ["synthetic_event_0"]
    assert first_page.next_cursor is not None
    assert second_page.next_cursor is None

    with pytest.raises(IdentityError, match="Security event cursor is invalid"):
        list_security_events(owner_profile, cursor=first_page.next_cursor + "tampered", limit=2)


@pytest.mark.parametrize("limit", [0, 101])
def test_security_event_page_limit_is_bounded(
    owner_profile: AdministratorProfile,
    limit: int,
) -> None:
    with pytest.raises(IdentityError, match="Security event page size is invalid"):
        list_security_events(owner_profile, cursor=None, limit=limit)
