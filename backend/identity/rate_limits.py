import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.core.cache import cache

from identity.exceptions import IdentityRateLimited, IdentityUnavailable

PASSWORD_LIMIT = 5
PASSWORD_WINDOW_SECONDS = 5 * 60
RECOVERY_LIMIT = 5
RECOVERY_WINDOW_SECONDS = 15 * 60


@dataclass(frozen=True)
class RateLimitScope:
    cache_key: str


def _scope_key(kind: str, *values: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        ("civicloop.identity.rate.v1\0" + "\0".join(values)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"civicloop:identity:rate:{kind}:{digest}"


def _check_limit(*, scope: RateLimitScope, maximum: int, window_seconds: int) -> RateLimitScope:
    try:
        added = cache.add(scope.cache_key, 1, timeout=window_seconds)
        attempt_count = 1 if added else cache.incr(scope.cache_key)
    except Exception:
        raise IdentityUnavailable() from None
    if attempt_count > maximum:
        raise IdentityRateLimited(window_seconds)
    return scope


def check_password_limit(
    *,
    normalized_owner: str,
    source_ip: str | None,
) -> RateLimitScope:
    scope = RateLimitScope(
        _scope_key(
            "password",
            normalized_owner.casefold().strip(),
            source_ip or "unknown",
        )
    )
    return _check_limit(
        scope=scope,
        maximum=PASSWORD_LIMIT,
        window_seconds=PASSWORD_WINDOW_SECONDS,
    )


def check_recovery_limit(
    *,
    owner_id: UUID,
    source_ip: str | None,
) -> RateLimitScope:
    scope = RateLimitScope(
        _scope_key("recovery", str(owner_id), source_ip or "unknown")
    )
    return _check_limit(
        scope=scope,
        maximum=RECOVERY_LIMIT,
        window_seconds=RECOVERY_WINDOW_SECONDS,
    )


def record_limit_success(scope: RateLimitScope) -> None:
    try:
        cache.delete(scope.cache_key)
    except Exception:
        raise IdentityUnavailable() from None
