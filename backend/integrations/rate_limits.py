import hashlib
import hmac
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.core.cache import cache

IntegrationAction = Literal["credential", "configuration", "test", "disable"]


class IntegrationRateLimited(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Too many integration administration attempts.")


class IntegrationRateLimitUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("Integration rate limiting is temporarily unavailable.")


@dataclass(frozen=True)
class RateLimit:
    maximum: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitScope:
    counter_key: str
    blocked_key: str


LIMITS: dict[IntegrationAction, RateLimit] = {
    "credential": RateLimit(maximum=5, window_seconds=5 * 60),
    "configuration": RateLimit(maximum=20, window_seconds=60),
    "test": RateLimit(maximum=10, window_seconds=60),
    "disable": RateLimit(maximum=5, window_seconds=5 * 60),
}


def _scope_key(action: IntegrationAction, *values: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        ("civicloop.integrations.rate.v1\0" + "\0".join(values)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"civicloop:integrations:rate:{action}:{digest}"


def check_integration_limit(
    *,
    action: IntegrationAction,
    owner_id: UUID,
    provider: str,
    source_ip: str | None,
) -> RateLimitScope:
    limit = LIMITS[action]
    base_key = _scope_key(action, str(owner_id), provider, source_ip or "unknown")
    scope = RateLimitScope(counter_key=f"{base_key}:count", blocked_key=f"{base_key}:blocked")
    try:
        if cache.get(scope.blocked_key) is not None:
            raise IntegrationRateLimited(limit.window_seconds)
        added = cache.add(scope.counter_key, 1, timeout=limit.window_seconds)
        attempt_count = 1 if added else cache.incr(scope.counter_key)
        if attempt_count > limit.maximum:
            cache.set(scope.counter_key, limit.maximum + 1, timeout=limit.window_seconds)
            cache.add(
                scope.blocked_key,
                limit.window_seconds,
                timeout=limit.window_seconds,
            )
    except IntegrationRateLimited:
        raise
    except Exception:
        raise IntegrationRateLimitUnavailable() from None
    if attempt_count > limit.maximum:
        raise IntegrationRateLimited(limit.window_seconds)
    return scope
