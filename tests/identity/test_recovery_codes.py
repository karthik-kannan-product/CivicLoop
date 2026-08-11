import re

import pytest
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import User
from django.utils import timezone
from identity.exceptions import IdentityError
from identity.models import AdministratorProfile, RecoveryCode
from identity.services.credentials import consume_recovery_code, generate_recovery_batch


@pytest.fixture
def owner_profile(db) -> AdministratorProfile:
    user = User.objects.create_user(username="synthetic.recovery.owner")
    return AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )


def test_generates_ten_recovery_codes_and_persists_only_hashes(
    owner_profile: AdministratorProfile,
) -> None:
    codes = generate_recovery_batch(owner_profile, now=timezone.now())

    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(re.fullmatch(r"[A-Z2-7]{8}-[A-Z2-7]{26}", code) for code in codes)
    stored = list(RecoveryCode.objects.filter(profile=owner_profile).order_by("public_id"))
    assert len(stored) == 10
    assert len({code.batch_id for code in stored}) == 1
    assert all(code.encoded_secret not in codes for code in stored)
    assert all(
        check_password(
            next(value for value in codes if value.startswith(f"{code.public_id}-")),
            code.encoded_secret,
        )
        for code in stored
    )


def test_new_recovery_batch_invalidates_every_unused_old_code(
    owner_profile: AdministratorProfile,
) -> None:
    now = timezone.now()
    old_codes = generate_recovery_batch(owner_profile, now=now)
    new_codes = generate_recovery_batch(owner_profile, now=now)

    old_ids = [code.split("-", 1)[0] for code in old_codes]
    assert RecoveryCode.objects.filter(public_id__in=old_ids, invalidated_at=now).count() == 10
    assert len(new_codes) == 10


def test_recovery_code_is_consumed_once(
    owner_profile: AdministratorProfile,
) -> None:
    now = timezone.now()
    presented = generate_recovery_batch(owner_profile, now=now)[0]

    consumed = consume_recovery_code(owner_profile, presented, now=now)

    assert consumed.consumed_at == now
    with pytest.raises(IdentityError, match="Verification failed"):
        consume_recovery_code(owner_profile, presented, now=now)


@pytest.mark.parametrize(
    "presented",
    ["", "not-a-code", "AB12CD34-too-short", "ZZZZZZZZ-AAAAAAAAAAAAAAAAAAAAAAAAAA"],
)
def test_malformed_or_unknown_recovery_code_uses_generic_failure(
    owner_profile: AdministratorProfile,
    presented: str,
) -> None:
    with pytest.raises(IdentityError, match="Verification failed"):
        consume_recovery_code(owner_profile, presented, now=timezone.now())
