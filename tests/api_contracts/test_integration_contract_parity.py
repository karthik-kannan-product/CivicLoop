import json
from pathlib import Path

import yaml
from django.test import Client, override_settings
from django.urls import resolve
from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "integrations"
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"

INTEGRATION_OPERATIONS = {
    "/api/v1/admin/integrations": {"get"},
    "/api/v1/admin/integrations/status": {"get"},
    "/api/v1/admin/integrations/{provider}/credential": {"put"},
    "/api/v1/admin/integrations/{provider}/configuration": {"patch"},
    "/api/v1/admin/integrations/{provider}/test": {"post"},
    "/api/v1/admin/integrations/{provider}/disable": {"post"},
    "/api/v1/admin/integrations/{provider}/audit": {"get"},
}

LIVE_INTEGRATION_ROUTES = {
    "/api/v1/admin/integrations": (
        "/api/v1/admin/integrations",
        "admin-integration-list",
    ),
    "/api/v1/admin/integrations/status": (
        "/api/v1/admin/integrations/status",
        "admin-integrations-readiness",
    ),
    "/api/v1/admin/integrations/{provider}/credential": (
        "/api/v1/admin/integrations/eventbrite/credential",
        "admin-integration-credential",
    ),
    "/api/v1/admin/integrations/{provider}/configuration": (
        "/api/v1/admin/integrations/eventbrite/configuration",
        "admin-integration-configuration",
    ),
    "/api/v1/admin/integrations/{provider}/test": (
        "/api/v1/admin/integrations/eventbrite/test",
        "admin-integration-test",
    ),
    "/api/v1/admin/integrations/{provider}/disable": (
        "/api/v1/admin/integrations/eventbrite/disable",
        "admin-integration-disable",
    ),
    "/api/v1/admin/integrations/{provider}/audit": (
        "/api/v1/admin/integrations/eventbrite/audit",
        "admin-integration-audit",
    ),
}

EXPECTED_SCHEMAS = {
    "connection.schema.json",
    "mutations.schema.json",
    "health-check.schema.json",
}
SENSITIVE_FIELD_NAMES = {
    "ciphertext",
    "credential",
    "fingerprint",
    "key_id",
    "last_four",
    "masked",
    "nonce",
    "plaintext",
    "secret",
    "token",
    "value",
    "provider_response",
    "response_body",
}


def load_schema(name: str) -> dict:
    with (SCHEMA_ROOT / name).open(encoding="utf-8") as source:
        return json.load(source)


def test_openapi_has_exact_integration_operation_surface() -> None:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    assert {
        path: set(specification["paths"][path]) & {"get", "post", "put", "patch", "delete"}
        for path in INTEGRATION_OPERATIONS
    } == INTEGRATION_OPERATIONS


def test_openapi_integration_surface_matches_live_django_routes_and_methods() -> None:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    documented_paths = {
        path for path in specification["paths"] if path.startswith("/api/v1/admin/integrations")
    }
    assert documented_paths == set(LIVE_INTEGRATION_ROUTES)
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
    ):
        for documented_path, (live_path, expected_view_name) in LIVE_INTEGRATION_ROUTES.items():
            assert resolve(live_path).view_name == expected_view_name
            response = Client().options(live_path)
            assert response.status_code == 405
            assert {method.strip().lower() for method in response["Allow"].split(",")} == (
                INTEGRATION_OPERATIONS[documented_path]
            )


def test_integration_schemas_are_closed_draft_2020_12_contracts() -> None:
    assert {path.name for path in SCHEMA_ROOT.glob("*.schema.json")} == EXPECTED_SCHEMAS
    for name in EXPECTED_SCHEMAS:
        schema = load_schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


def test_responses_cannot_contain_credential_bearing_fields() -> None:
    for name in EXPECTED_SCHEMAS:
        schema = load_schema(name)
        _assert_no_sensitive_response_fields(schema)


def _assert_no_sensitive_response_fields(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            response_properties = {
                field
                for field, field_schema in properties.items()
                if not isinstance(field_schema, dict) or not field_schema.get("writeOnly", False)
            }
            assert not response_properties & SENSITIVE_FIELD_NAMES
        for nested in value.values():
            _assert_no_sensitive_response_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_sensitive_response_fields(nested)


def test_mutation_operations_use_csrf_no_store_and_expected_versions() -> None:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    for path, methods in INTEGRATION_OPERATIONS.items():
        for method in methods:
            operation = specification["paths"][path][method]
            if method == "get":
                continue
            assert operation["responses"]["200"]["headers"]["Cache-Control"]["$ref"] == (
                "#/components/headers/NoStore"
            )
            assert {parameter["$ref"] for parameter in operation.get("parameters", [])} >= {
                "#/components/parameters/CsrfToken"
            }
            request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
            assert request_schema["$ref"].startswith(
                "../schemas/integrations/mutations.schema.json#/$defs/"
            )
            assert operation["responses"]["429"]["$ref"] == (
                "#/components/responses/RateLimitedProblemResponse"
            )
            assert operation["responses"]["503"]["$ref"] == (
                "#/components/responses/ProblemResponse"
            )


def test_audit_contract_supports_bounded_administrative_denials() -> None:
    audit_event = load_schema("health-check.schema.json")["$defs"]["IntegrationAuditEvent"]

    assert set(audit_event["properties"]["action"]["enum"]) >= {
        "credential_replaced",
        "configuration_changed",
        "connection_tested",
        "connection_disabled",
        "audit_listed",
    }
    assert set(audit_event["properties"]["failure_category"]["enum"]) >= {
        None,
        "authentication",
        "freshness",
        "recovery_restricted",
        "rate_limit",
        "rate_limit_unavailable",
        "version_conflict",
        "key_unavailable",
        "provider_not_found",
    }
    assert set(audit_event["properties"]["version"]["type"]) == {"integer", "null"}
