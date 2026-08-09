import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from identity.exceptions import IdentityError
from identity.models import AdministratorProfile, AdministratorSecurityEvent

SENSITIVE_DETAIL_KEYS = frozenset(
    {
        "authorization",
        "ciphertext",
        "cookie",
        "encrypted_seed",
        "key_id",
        "manual_secret",
        "otp",
        "password",
        "recovery_code",
        "recovery_codes",
        "secret",
        "seed",
        "session_key",
        "token",
        "totp",
        "totp_token",
        "user_agent",
    }
)
ACTION_PATTERN = re.compile(r"^[a-z0-9_.-]{1,100}$")
DETAIL_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_DETAIL_DEPTH = 3
MAX_DETAIL_ITEMS = 50
MAX_DETAIL_STRING_LENGTH = 256
MAX_SERIALIZED_DETAIL_BYTES = 4096
CURSOR_SALT = "civicloop.identity.security-events.v1"


@dataclass(frozen=True)
class SecurityEventPage:
    events: tuple[AdministratorSecurityEvent, ...]
    next_cursor: str | None


def _invalid_details() -> IdentityError:
    return IdentityError("Security event details are invalid.")


def _sanitize_detail(value: object, *, depth: int, count: list[int]) -> object:
    count[0] += 1
    if count[0] > MAX_DETAIL_ITEMS or depth > MAX_DETAIL_DEPTH:
        raise _invalid_details()
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        if len(value) > MAX_DETAIL_STRING_LENGTH:
            raise _invalid_details()
        return value
    if isinstance(value, list | tuple):
        return [_sanitize_detail(item, depth=depth + 1, count=count) for item in value]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or DETAIL_KEY_PATTERN.fullmatch(key) is None:
                raise _invalid_details()
            normalized_key = key.casefold().replace("-", "_").replace(".", "_")
            if normalized_key in SENSITIVE_DETAIL_KEYS:
                raise _invalid_details()
            result[key] = _sanitize_detail(item, depth=depth + 1, count=count)
        return result
    raise _invalid_details()


def _safe_details(details: dict[str, object] | None) -> dict[str, object]:
    sanitized = _sanitize_detail(details or {}, depth=0, count=[0])
    if not isinstance(sanitized, dict):
        raise _invalid_details()
    encoded = json.dumps(sanitized, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(encoded) > MAX_SERIALIZED_DETAIL_BYTES:
        raise _invalid_details()
    return sanitized


def record_security_event(
    *,
    action: str,
    outcome: str,
    owner: AdministratorProfile | None,
    source_ip: str | None,
    session_id: UUID | None,
    target_type: str = "",
    target_id: str = "",
    details: dict[str, object] | None = None,
) -> AdministratorSecurityEvent:
    if ACTION_PATTERN.fullmatch(action) is None:
        raise IdentityError("Security event action is invalid.")
    if outcome not in AdministratorSecurityEvent.Outcome.values:
        raise IdentityError("Security event outcome is invalid.")
    if len(target_type) > 64 or len(target_id) > 100:
        raise IdentityError("Security event target is invalid.")
    return AdministratorSecurityEvent.objects.create(
        profile=owner,
        user=owner.user if owner is not None else None,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
        details=_safe_details(details),
        source_ip=source_ip,
        session_id=session_id,
    )


def _encode_cursor(event: AdministratorSecurityEvent) -> str:
    return signing.dumps(
        [event.created_at.isoformat(), str(event.id)],
        salt=CURSOR_SALT,
        compress=True,
    )


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        values: Any = signing.loads(cursor, salt=CURSOR_SALT)
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError("Invalid cursor")
        created_at = parse_datetime(values[0]) if isinstance(values[0], str) else None
        event_id = UUID(values[1]) if isinstance(values[1], str) else None
        if created_at is None or event_id is None or created_at.tzinfo is None:
            raise ValueError("Invalid cursor")
        return created_at, event_id
    except (signing.BadSignature, TypeError, ValueError):
        raise IdentityError("Security event cursor is invalid.") from None


def list_security_events(
    owner: AdministratorProfile,
    *,
    cursor: str | None,
    limit: int,
) -> SecurityEventPage:
    if not 1 <= limit <= 100:
        raise IdentityError("Security event page size is invalid.")
    query = AdministratorSecurityEvent.objects.filter(profile=owner)
    if cursor is not None:
        created_at, event_id = _decode_cursor(cursor)
        query = query.filter(
            Q(created_at__lt=created_at)
            | Q(created_at=created_at, id__lt=event_id)
        )
    rows = list(query.order_by("-created_at", "-id")[: limit + 1])
    page_rows = tuple(rows[:limit])
    next_cursor = _encode_cursor(page_rows[-1]) if len(rows) > limit else None
    return SecurityEventPage(events=page_rows, next_cursor=next_cursor)
