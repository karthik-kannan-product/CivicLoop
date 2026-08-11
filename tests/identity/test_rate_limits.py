from uuid import UUID

import pytest
from django.core.cache import cache
from django.test import override_settings
from identity.exceptions import IdentityRateLimited, IdentityUnavailable
from identity.rate_limits import (
    check_password_limit,
    check_recovery_limit,
    record_limit_success,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "identity-rate-limit-tests",
    }
}


@pytest.fixture(autouse=True)
def clear_test_cache():
    with override_settings(CACHES=LOCMEM_CACHE):
        cache.clear()
        yield
        cache.clear()


@override_settings(CACHES=LOCMEM_CACHE)
def test_password_limit_uses_hmac_key_without_raw_identity_or_ip() -> None:
    scope = check_password_limit(
        normalized_owner="synthetic.owner@example.test",
        source_ip="192.0.2.10",
    )

    assert "synthetic.owner" not in scope.cache_key
    assert "192.0.2.10" not in scope.cache_key
    assert scope.cache_key.startswith("civicloop:identity:rate:password:")


@override_settings(CACHES=LOCMEM_CACHE)
def test_password_limit_allows_five_attempts_then_returns_retry_after() -> None:
    for _ in range(5):
        check_password_limit(normalized_owner="synthetic.owner", source_ip="192.0.2.10")

    with pytest.raises(IdentityRateLimited) as raised:
        check_password_limit(normalized_owner="synthetic.owner", source_ip="192.0.2.10")

    assert raised.value.retry_after_seconds == 300
    assert str(raised.value) == "Too many verification attempts."


@override_settings(CACHES=LOCMEM_CACHE)
def test_success_reset_clears_the_rate_limit_scope() -> None:
    scope = check_password_limit(normalized_owner="synthetic.owner", source_ip=None)
    for _ in range(4):
        check_password_limit(normalized_owner="synthetic.owner", source_ip=None)

    record_limit_success(scope)

    check_password_limit(normalized_owner="synthetic.owner", source_ip=None)


@override_settings(CACHES=LOCMEM_CACHE)
def test_recovery_limit_is_scoped_by_owner_and_ip() -> None:
    owner_id = UUID("4d815a9e-19f6-4485-a18d-f1ccf91b6ee2")
    first = check_recovery_limit(owner_id=owner_id, source_ip="2001:db8::1")
    second = check_recovery_limit(owner_id=owner_id, source_ip="2001:db8::2")

    assert first.cache_key != second.cache_key
    assert str(owner_id) not in first.cache_key
    assert "2001:db8" not in first.cache_key


def test_cache_outage_fails_closed_without_exposing_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic valkey connection detail")

    monkeypatch.setattr("identity.rate_limits.cache.add", unavailable)

    with pytest.raises(IdentityUnavailable) as raised:
        check_password_limit(normalized_owner="synthetic.owner", source_ip="192.0.2.10")

    assert str(raised.value) == "Administrator authentication is temporarily unavailable."
    assert "valkey" not in str(raised.value).lower()
