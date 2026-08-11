import base64
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_otp.oath import TOTP

from identity.crypto import decrypt_totp_seed, encrypt_totp_seed
from identity.exceptions import IdentityError
from identity.models import (
    AdministratorProfile,
    AdministratorSession,
    AdministratorTOTPDevice,
    RecoveryCode,
)
from identity.services.security import record_security_event

RECOVERY_CODE_PATTERN = re.compile(r"^(?P<public_id>[A-Z2-7]{8})-(?P<secret>[A-Z2-7]{26})$")
RECOVERY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
DUMMY_RECOVERY_HASH = make_password(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class TOTPEnrollment:
    device: AdministratorTOTPDevice
    otpauth_uri: str
    manual_secret: str


def _verification_failed() -> IdentityError:
    return IdentityError("Verification failed.")


def _base32(value: bytes) -> str:
    return base64.b32encode(value).rstrip(b"=").decode("ascii")


@transaction.atomic
def begin_totp_enrollment(
    profile: AdministratorProfile,
    *,
    label: str,
    now: datetime,
) -> TOTPEnrollment:
    normalized_label = label.strip()
    if not 1 <= len(normalized_label) <= 64:
        raise IdentityError("Authenticator label is invalid.")
    AdministratorTOTPDevice.objects.filter(
        profile=profile,
        confirmed=False,
        disabled_at__isnull=True,
    ).update(disabled_at=now)
    device_id = uuid.uuid4()
    seed = secrets.token_bytes(20)
    manual_secret = _base32(seed)
    device = AdministratorTOTPDevice.objects.create(
        id=device_id,
        user=profile.user,
        profile=profile,
        name=normalized_label,
        confirmed=False,
        seed_envelope=encrypt_totp_seed(
            seed,
            owner_id=profile.id,
            device_id=device_id,
        ),
    )
    account_label = quote(f"CivicLoop:{profile.user.username}", safe="")
    query = urlencode(
        {
            "secret": manual_secret,
            "issuer": "CivicLoop",
            "algorithm": "SHA1",
            "digits": 6,
            "period": 30,
        }
    )
    return TOTPEnrollment(
        device=device,
        otpauth_uri=f"otpauth://totp/{account_label}?{query}",
        manual_secret=manual_secret,
    )


def _throttle_allows(device: AdministratorTOTPDevice, *, now: datetime) -> bool:
    if device.throttling_failure_count == 0 or device.throttling_failure_timestamp is None:
        return True
    delay = device.get_throttle_factor() * (2 ** (device.throttling_failure_count - 1))
    return now >= device.throttling_failure_timestamp + timedelta(seconds=delay)


@transaction.atomic
def _verify_totp_transaction(
    device: AdministratorTOTPDevice,
    token: str,
    *,
    now: datetime,
    allow_unconfirmed: bool = False,
) -> AdministratorTOTPDevice | None:
    locked = (
        AdministratorTOTPDevice.objects.select_for_update()
        .select_related("profile")
        .get(pk=device.pk)
    )
    if (
        locked.disabled_at is not None
        or locked.replaced_at is not None
        or (not locked.confirmed and not allow_unconfirmed)
        or not _throttle_allows(locked, now=now)
    ):
        return None
    verified = False
    generator: TOTP | None = None
    if re.fullmatch(r"[0-9]{6}", token):
        seed = decrypt_totp_seed(
            locked.seed_envelope,
            owner_id=locked.profile_id,
            device_id=locked.id,
        )
        generator = TOTP(seed, locked.step, 0, locked.digits, locked.drift)
        generator.time = now.timestamp()
        verified = generator.verify(int(token), tolerance=1, min_t=locked.last_t + 1)
    if not verified or generator is None:
        locked.throttling_failure_count += 1
        locked.throttling_failure_timestamp = now
        locked.save(
            update_fields=("throttling_failure_count", "throttling_failure_timestamp")
        )
        return None
    locked.last_t = generator.t()
    locked.drift = generator.drift
    locked.last_used_at = now
    locked.throttling_failure_count = 0
    locked.throttling_failure_timestamp = None
    locked.save(
        update_fields=(
            "last_t",
            "drift",
            "last_used_at",
            "throttling_failure_count",
            "throttling_failure_timestamp",
        )
    )
    return locked


def verify_totp(
    device: AdministratorTOTPDevice,
    token: str,
    *,
    now: datetime,
    allow_unconfirmed: bool = False,
) -> AdministratorTOTPDevice:
    verified = _verify_totp_transaction(
        device,
        token,
        now=now,
        allow_unconfirmed=allow_unconfirmed,
    )
    if verified is None:
        raise _verification_failed()
    return verified


def _new_public_recovery_id(reserved: set[str]) -> str:
    while True:
        candidate = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(8))
        is_available = candidate not in reserved and not RecoveryCode.objects.filter(
            public_id=candidate
        ).exists()
        if is_available:
            reserved.add(candidate)
            return candidate


@transaction.atomic
def generate_recovery_batch(
    profile: AdministratorProfile,
    *,
    now: datetime,
) -> list[str]:
    RecoveryCode.objects.filter(
        profile=profile,
        consumed_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=now)
    batch_id = uuid.uuid4()
    plaintext_codes: list[str] = []
    rows: list[RecoveryCode] = []
    reserved: set[str] = set()
    for _ in range(10):
        public_id = _new_public_recovery_id(reserved)
        secret = _base32(secrets.token_bytes(16))
        plaintext = f"{public_id}-{secret}"
        plaintext_codes.append(plaintext)
        rows.append(
            RecoveryCode(
                profile=profile,
                batch_id=batch_id,
                public_id=public_id,
                encoded_secret=make_password(plaintext),
            )
        )
    RecoveryCode.objects.bulk_create(rows)
    return plaintext_codes


@transaction.atomic
def consume_recovery_code(
    profile: AdministratorProfile,
    presented_code: str,
    *,
    now: datetime,
) -> RecoveryCode:
    normalized = presented_code.strip().upper()
    match = RECOVERY_CODE_PATTERN.fullmatch(normalized)
    if match is None:
        check_password(normalized, DUMMY_RECOVERY_HASH)
        raise _verification_failed()
    try:
        recovery_code = RecoveryCode.objects.select_for_update().get(
            profile=profile,
            public_id=match.group("public_id"),
            consumed_at__isnull=True,
            invalidated_at__isnull=True,
        )
    except RecoveryCode.DoesNotExist:
        check_password(normalized, DUMMY_RECOVERY_HASH)
        raise _verification_failed() from None
    if not check_password(normalized, recovery_code.encoded_secret):
        raise _verification_failed()
    recovery_code.consumed_at = now
    recovery_code.save(update_fields=("consumed_at",))
    return recovery_code


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
