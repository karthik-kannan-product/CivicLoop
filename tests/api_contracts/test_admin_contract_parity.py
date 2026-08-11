import json
from pathlib import Path

import pytest
import yaml
from django.test import Client, override_settings
from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "admin"
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"

ADMIN_OPERATIONS = {
    "/api/v1/admin/security/status": {"get"},
    "/api/v1/admin/auth/password": {"post"},
    "/api/v1/admin/auth/totp": {"post"},
    "/api/v1/admin/auth/recovery": {"post"},
    "/api/v1/admin/auth/logout": {"post"},
    "/api/v1/admin/security/totp/enrollment": {"post"},
    "/api/v1/admin/security/totp/confirmation": {"post"},
    "/api/v1/admin/security/reauthentication": {"post"},
    "/api/v1/admin/security/password": {"put"},
    "/api/v1/admin/security/recovery-codes/regeneration": {"post"},
    "/api/v1/admin/security/sessions": {"get"},
    "/api/v1/admin/security/sessions/revoke-others": {"post"},
    "/api/v1/admin/security/sessions/{sessionId}/revocation": {"post"},
    "/api/v1/admin/security/events": {"get"},
}

EXPECTED_SCHEMAS = {
    "auth-status-response.schema.json",
    "confirmation-request.schema.json",
    "confirmation-response.schema.json",
    "enrollment-request.schema.json",
    "enrollment-response.schema.json",
    "event-page.schema.json",
    "password-change-request.schema.json",
    "password-change-response.schema.json",
    "password-challenge-request.schema.json",
    "password-challenge-response.schema.json",
    "reauthentication-request.schema.json",
    "reauthentication-response.schema.json",
    "recovery-challenge-request.schema.json",
    "recovery-challenge-response.schema.json",
    "recovery-code-regeneration-response.schema.json",
    "revocation-response.schema.json",
    "revoke-others-response.schema.json",
    "session-list.schema.json",
    "totp-challenge-request.schema.json",
}


def load_schema(name: str) -> dict:
    with (SCHEMA_ROOT / name).open(encoding="utf-8") as source:
        return json.load(source)


def validate(payload: dict, name: str) -> None:
    Draft202012Validator(
        load_schema(name),
        format_checker=FormatChecker(),
    ).validate(payload)


def test_openapi_has_exact_administrator_operation_surface() -> None:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    assert {
        path: set(specification["paths"][path]) & {"get", "post", "put", "patch", "delete"}
        for path in ADMIN_OPERATIONS
    } == ADMIN_OPERATIONS


def test_every_administrator_schema_is_draft_2020_12_and_expected() -> None:
    assert {path.name for path in SCHEMA_ROOT.glob("*.schema.json")} == EXPECTED_SCHEMAS
    for name in EXPECTED_SCHEMAS:
        schema = load_schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


def test_sensitive_fields_have_directional_openapi_annotations() -> None:
    request_fields = {
        "password-challenge-request.schema.json": ["password"],
        "totp-challenge-request.schema.json": ["token"],
        "recovery-challenge-request.schema.json": ["recovery_code"],
        "confirmation-request.schema.json": ["token"],
        "reauthentication-request.schema.json": ["password", "token"],
        "password-change-request.schema.json": ["current_password", "new_password"],
    }
    for name, fields in request_fields.items():
        schema = load_schema(name)
        assert all(schema["properties"][field]["writeOnly"] is True for field in fields)
    for name in [
        "enrollment-response.schema.json",
        "confirmation-response.schema.json",
        "recovery-code-regeneration-response.schema.json",
    ]:
        schema = load_schema(name)
        sensitive = {"manual_secret", "recovery_codes"} & set(schema["properties"])
        assert all(schema["properties"][field]["readOnly"] is True for field in sensitive)


def test_state_changes_require_csrf_and_sensitive_successes_are_no_store() -> None:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    for path, methods in ADMIN_OPERATIONS.items():
        for method in methods:
            operation = specification["paths"][path][method]
            success = operation["responses"]["200"]
            assert success["headers"]["Cache-Control"]["$ref"] == (
                "#/components/headers/NoStore"
            )
            if method != "get":
                assert {parameter["$ref"] for parameter in operation.get("parameters", [])} >= {
                    "#/components/parameters/CsrfToken"
                }


@pytest.mark.django_db
@override_settings(CIVICLOOP_ADMIN_IDENTITY_ENABLED=True)
def test_live_anonymous_status_and_problem_payloads_match_contracts() -> None:
    client = Client()
    status = client.get("/api/v1/admin/security/status")
    problem = client.post(
        "/api/v1/admin/auth/password",
        data=json.dumps({}),
        content_type="application/json",
    )

    validate(status.json(), "auth-status-response.schema.json")
    assert problem.status_code == 400
    with (REPOSITORY_ROOT / "schemas" / "api" / "problem-details.schema.json").open(
        encoding="utf-8"
    ) as source:
        Draft202012Validator(json.load(source), format_checker=FormatChecker()).validate(
            problem.json()
        )
