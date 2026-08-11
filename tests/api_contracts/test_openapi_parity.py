import json
from pathlib import Path

import pytest
import yaml
from django.test import Client
from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"


def load_schema(relative_path: str) -> dict:
    with (SCHEMA_ROOT / relative_path).open(encoding="utf-8") as source:
        return json.load(source)


def validate(payload: dict, relative_path: str) -> None:
    validator = Draft202012Validator(
        load_schema(relative_path),
        format_checker=FormatChecker(),
    )
    validator.validate(payload)


def post_json(client: Client, path: str, body: dict[str, object] | None = None):
    return client.post(path, data=json.dumps(body or {}), content_type="application/json")


def test_openapi_documents_every_current_application_endpoint() -> None:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    assert set(specification["paths"]) == {
        "/api/v1/auth/session",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/demo",
        "/api/v1/demo/reset",
        "/api/v1/workflows/{workflowId}/runs",
        "/api/v1/workflows/{workflowId}/answers",
        "/api/v1/workflows/{workflowId}/submit",
        "/api/v1/approvals/{approvalId}/decision",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/admin/security/status",
        "/api/v1/admin/auth/password",
        "/api/v1/admin/auth/totp",
        "/api/v1/admin/auth/recovery",
        "/api/v1/admin/auth/logout",
        "/api/v1/admin/security/totp/enrollment",
        "/api/v1/admin/security/totp/confirmation",
        "/api/v1/admin/security/reauthentication",
        "/api/v1/admin/security/password",
        "/api/v1/admin/security/recovery-codes/regeneration",
        "/api/v1/admin/security/sessions",
        "/api/v1/admin/security/sessions/revoke-others",
        "/api/v1/admin/security/sessions/{sessionId}/revocation",
        "/api/v1/admin/security/events",
    }


@pytest.mark.django_db
def test_session_and_demo_responses_match_checked_in_schemas() -> None:
    client = Client()
    login = post_json(
        client,
        "/api/v1/auth/login",
        {"username": "maya.operator", "password": "civicloop-demo"},
    )
    validate(login.json(), "api/session-response.schema.json")

    state = client.get("/api/v1/demo")
    validate(state.json(), "launchloop/demo-state.schema.json")


@pytest.mark.django_db
def test_problem_response_matches_checked_in_schema() -> None:
    response = Client().get("/api/v1/demo")

    validate(response.json(), "api/problem-details.schema.json")
