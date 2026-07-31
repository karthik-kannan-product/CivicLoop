from unittest.mock import patch

from django.test import Client

from health.checks import DependencyStatus


def test_liveness_does_not_call_dependencies() -> None:
    response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("health.views.readiness_status")
def test_readiness_returns_200_when_dependencies_are_ready(mock_status) -> None:
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
def test_readiness_returns_503_without_leaking_exception_text(mock_status) -> None:
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
