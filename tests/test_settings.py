import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_SECRET_KEY = "development-only-not-for-production"


def run_settings_command(
    command: str = "import civicloop.settings", **environment_overrides: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "backend")
    environment["DATABASE_URL"] = "sqlite:///:memory:"
    environment.pop("DJANGO_SECRET_KEY", None)
    environment.update(environment_overrides)
    return subprocess.run(
        [sys.executable, "-c", command],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_test_environment_uses_in_memory_database() -> None:
    assert settings.ENVIRONMENT == "test"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.DATABASES["default"]["NAME"] == ":memory:"


def test_default_agent_concurrency_never_exceeds_three() -> None:
    assert 1 <= settings.AGENT_MAX_CONCURRENCY <= 3


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


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_environments_allow_development_secret_key(environment: str) -> None:
    result = run_settings_command(CIVICLOOP_ENV=environment)

    assert result.returncode == 0, result.stderr


def test_production_allows_configured_secret_key() -> None:
    result = run_settings_command(
        CIVICLOOP_ENV="production",
        DJANGO_SECRET_KEY="synthetic-production-secret-for-settings-test",
    )

    assert result.returncode == 0, result.stderr


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
