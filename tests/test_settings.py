import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_SECRET_KEY = "development-only-not-for-production"
DOCUMENTED_PLACEHOLDER_SECRET_KEY = "replace-with-at-least-50-random-characters"
SETTINGS_ENVIRONMENT_VARIABLES = (
    "CIVICLOOP_ENV",
    "CIVICLOOP_DEMO_PASSWORD",
    "DJANGO_SECRET_KEY",
    "DATABASE_URL",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "DJANGO_SECURE_HSTS_SECONDS",
    "VALKEY_URL",
    "AGENT_MAX_CONCURRENCY",
    "CELERY_BROKER_URL",
    "CELERY_WORKER_CONCURRENCY",
)


def run_settings_command(
    command: str = "import civicloop.settings", **environment_overrides: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for variable in SETTINGS_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "backend")
    environment["DATABASE_URL"] = "sqlite:///:memory:"
    environment.update(environment_overrides)
    return subprocess.run(
        [sys.executable, "-c", command],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_test_environment_uses_the_configured_database() -> None:
    assert settings.ENVIRONMENT == "test"
    database_url = os.environ["DATABASE_URL"]

    if database_url.startswith("sqlite:///"):
        assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
        assert settings.DATABASES["default"]["NAME"] in {
            ":memory:",
            "file:memorydb_default?mode=memory&cache=shared",
        }
    else:
        assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


def test_default_agent_concurrency_never_exceeds_three() -> None:
    assert 1 <= settings.AGENT_MAX_CONCURRENCY <= 3


def test_default_celery_worker_concurrency_is_within_agent_limit() -> None:
    assert 1 <= settings.CELERY_WORKER_CONCURRENCY <= settings.AGENT_MAX_CONCURRENCY


def test_documented_default_celery_broker_url_is_used_in_a_fresh_process() -> None:
    result = run_settings_command(
        "from civicloop.settings import CELERY_BROKER_URL; print(CELERY_BROKER_URL)",
        CIVICLOOP_ENV="test",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "redis://localhost:6379/1"


def test_production_rejects_missing_secret_key() -> None:
    result = run_settings_command(CIVICLOOP_ENV="production")

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY must be set to a non-default value" in result.stderr


def test_production_rejects_development_secret_key() -> None:
    result = run_settings_command(
        CIVICLOOP_ENV="production",
        DJANGO_SECRET_KEY=DEVELOPMENT_SECRET_KEY,
    )

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY must be set to a non-default value" in result.stderr


def test_production_rejects_documented_placeholder_secret_key() -> None:
    result = run_settings_command(
        CIVICLOOP_ENV="production",
        DJANGO_SECRET_KEY=DOCUMENTED_PLACEHOLDER_SECRET_KEY,
    )

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY must be set to a non-default value" in result.stderr


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_environments_allow_development_secret_key(environment: str) -> None:
    result = run_settings_command(CIVICLOOP_ENV=environment)

    assert result.returncode == 0, result.stderr


def test_production_allows_configured_secret_key() -> None:
    result = run_settings_command(
        CIVICLOOP_ENV="production",
        DJANGO_SECRET_KEY="synthetic-production-secret-for-settings-test",
        CIVICLOOP_DEMO_PASSWORD="unique-demo-password-for-settings-test",
    )

    assert result.returncode == 0, result.stderr


def test_production_rejects_missing_or_documented_demo_password() -> None:
    for demo_password in (None, "", "civicloop-demo", "replace-with-a-unique-demo-password"):
        overrides = {
            "CIVICLOOP_ENV": "production",
            "DJANGO_SECRET_KEY": "synthetic-production-secret-for-settings-test",
        }
        if demo_password is not None:
            overrides["CIVICLOOP_DEMO_PASSWORD"] = demo_password

        result = run_settings_command(**overrides)

        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert "CIVICLOOP_DEMO_PASSWORD must be set to a non-default value" in result.stderr


def test_production_enables_proxy_cookie_and_transport_security() -> None:
    result = run_settings_command(
        "from civicloop import settings; "
        "print(settings.SECURE_PROXY_SSL_HEADER); "
        "print(settings.SESSION_COOKIE_SECURE); "
        "print(settings.CSRF_COOKIE_SECURE); "
        "print(settings.SECURE_HSTS_SECONDS); "
        "print(settings.CSRF_TRUSTED_ORIGINS)",
        CIVICLOOP_ENV="production",
        DJANGO_SECRET_KEY="synthetic-production-secret-for-settings-test",
        CIVICLOOP_DEMO_PASSWORD="unique-demo-password-for-settings-test",
        DJANGO_CSRF_TRUSTED_ORIGINS="https://civicloop.example.test",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "('HTTP_X_FORWARDED_PROTO', 'https')",
        "True",
        "True",
        "31536000",
        "['https://civicloop.example.test']",
    ]


@pytest.mark.parametrize("value", ["", "not-an-integer", "3.5"])
def test_malformed_agent_concurrency_raises_clear_configuration_error(value: str) -> None:
    result = run_settings_command(CIVICLOOP_ENV="test", AGENT_MAX_CONCURRENCY=value)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "AGENT_MAX_CONCURRENCY must be an integer" in result.stderr


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("-1", "1"), ("2", "2"), ("10", "3")],
)
def test_valid_agent_concurrency_is_clamped(configured: str, expected: str) -> None:
    result = run_settings_command(
        "from civicloop.settings import AGENT_MAX_CONCURRENCY; print(AGENT_MAX_CONCURRENCY)",
        CIVICLOOP_ENV="test",
        AGENT_MAX_CONCURRENCY=configured,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("agent_maximum", "configured", "expected"),
    [("3", "-1", "1"), ("2", "2", "2"), ("2", "10", "2")],
)
def test_celery_worker_concurrency_is_clamped(
    agent_maximum: str, configured: str, expected: str
) -> None:
    result = run_settings_command(
        "from civicloop.settings import CELERY_WORKER_CONCURRENCY; "
        "print(CELERY_WORKER_CONCURRENCY)",
        CIVICLOOP_ENV="test",
        AGENT_MAX_CONCURRENCY=agent_maximum,
        CELERY_WORKER_CONCURRENCY=configured,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("value", ["", "not-an-integer", "3.5"])
def test_malformed_celery_worker_concurrency_raises_clear_configuration_error(value: str) -> None:
    result = run_settings_command(CIVICLOOP_ENV="test", CELERY_WORKER_CONCURRENCY=value)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "CELERY_WORKER_CONCURRENCY must be an integer" in result.stderr
