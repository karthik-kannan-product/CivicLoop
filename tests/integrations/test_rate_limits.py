from uuid import UUID

import pytest
from django.core.cache import cache
from django.test import override_settings
from integrations.rate_limits import (
    IntegrationRateLimited,
    IntegrationRateLimitUnavailable,
    check_integration_limit,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "integration-rate-limit-tests",
    }
}


@pytest.fixture(autouse=True)
def clear_test_cache():
    with override_settings(CACHES=LOCMEM_CACHE):
        cache.clear()
        yield
        cache.clear()


@override_settings(CACHES=LOCMEM_CACHE)
def test_credential_limit_is_bounded_and_uses_a_redacted_scope() -> None:
    owner_id = UUID("4d815a9e-19f6-4485-a18d-f1ccf91b6ee2")
    for _ in range(5):
        scope = check_integration_limit(
            action="credential",
            owner_id=owner_id,
            provider="eventbrite",
            source_ip="192.0.2.10",
        )

    with pytest.raises(IntegrationRateLimited) as first:
        check_integration_limit(
            action="credential",
            owner_id=owner_id,
            provider="eventbrite",
            source_ip="192.0.2.10",
        )
    with pytest.raises(IntegrationRateLimited) as repeated:
        check_integration_limit(
            action="credential",
            owner_id=owner_id,
            provider="eventbrite",
            source_ip="192.0.2.10",
        )

    assert first.value.retry_after_seconds == 300
    assert repeated.value.retry_after_seconds == 300
    assert cache.get(scope.counter_key) == 6
    assert str(owner_id) not in scope.counter_key
    assert "eventbrite" not in scope.counter_key
    assert "192.0.2.10" not in scope.counter_key


def test_cache_outage_fails_closed_without_exposing_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic valkey connection detail")

    monkeypatch.setattr("integrations.rate_limits.cache.get", unavailable)

    with pytest.raises(IntegrationRateLimitUnavailable) as raised:
        check_integration_limit(
            action="test",
            owner_id=UUID("4d815a9e-19f6-4485-a18d-f1ccf91b6ee2"),
            provider="openai",
            source_ip=None,
        )

    assert str(raised.value) == "Integration rate limiting is temporarily unavailable."
    assert "valkey" not in str(raised.value).lower()


@pytest.mark.parametrize(
    ("action", "maximum", "retry_after"),
    [
        ("credential", 5, 300),
        ("configuration", 20, 60),
        ("test", 10, 60),
        ("disable", 5, 300),
    ],
)
@override_settings(CACHES=LOCMEM_CACHE)
def test_each_integration_mutation_has_a_bounded_limit(
    action: str,
    maximum: int,
    retry_after: int,
) -> None:
    kwargs = {
        "action": action,
        "owner_id": UUID("4d815a9e-19f6-4485-a18d-f1ccf91b6ee2"),
        "provider": "eventbrite",
        "source_ip": "192.0.2.10",
    }
    for _ in range(maximum):
        check_integration_limit(**kwargs)

    with pytest.raises(IntegrationRateLimited) as raised:
        check_integration_limit(**kwargs)

    assert raised.value.retry_after_seconds == retry_after
