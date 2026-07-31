from unittest.mock import Mock, patch

from django.test import Client
from health.checks import (
    DependencyStatus,
    postgres_is_ready,
    valkey_is_ready,
)


@patch("health.views.readiness_status")
def test_liveness_does_not_call_dependencies(mock_status: Mock) -> None:
    response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    mock_status.assert_not_called()


@patch("health.views.readiness_status")
def test_readiness_returns_200_when_dependencies_are_ready(mock_status: Mock) -> None:
    mock_status.return_value = [
        DependencyStatus(name="postgres", ready=True),
        DependencyStatus(name="valkey", ready=True),
    ]

    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["dependencies"] == {
        "postgres": {"ready": True},
        "valkey": {"ready": True},
    }


@patch("health.views.readiness_status")
def test_readiness_returns_503_when_a_dependency_is_not_ready(mock_status: Mock) -> None:
    mock_status.return_value = [
        DependencyStatus(name="postgres", ready=False),
        DependencyStatus(name="valkey", ready=True),
    ]

    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {
            "postgres": {"ready": False},
            "valkey": {"ready": True},
        },
    }


@patch("health.checks.valkey_is_ready", return_value=True)
@patch("health.checks.connections")
def test_postgres_check_failure_is_not_disclosed_by_readiness(
    mock_connections: Mock, _mock_valkey_is_ready: Mock
) -> None:
    exception_message = "synthetic-postgres-password"
    mock_connections.__getitem__.return_value.cursor.side_effect = RuntimeError(exception_message)

    assert postgres_is_ready() is False

    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["postgres"] == {"ready": False}
    assert exception_message not in response.content.decode()


@patch("health.checks.postgres_is_ready", return_value=True)
@patch("health.checks.caches")
def test_valkey_check_failure_is_not_disclosed_by_readiness(
    mock_caches: Mock, _mock_postgres_is_ready: Mock
) -> None:
    exception_message = "synthetic-valkey-password"
    mock_caches.__getitem__.side_effect = RuntimeError(exception_message)

    assert valkey_is_ready() is False

    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["valkey"] == {"ready": False}
    assert exception_message not in response.content.decode()
