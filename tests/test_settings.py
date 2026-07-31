from django.conf import settings


def test_test_environment_uses_in_memory_database() -> None:
    assert settings.ENVIRONMENT == "test"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.DATABASES["default"]["NAME"] == ":memory:"


def test_default_agent_concurrency_never_exceeds_three() -> None:
    assert 1 <= settings.AGENT_MAX_CONCURRENCY <= 3
