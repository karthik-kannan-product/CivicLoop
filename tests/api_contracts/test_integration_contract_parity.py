import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "integrations"
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"

INTEGRATION_OPERATIONS = {
    "/api/v1/admin/integrations": {"get"},
    "/api/v1/admin/integrations/{provider}/credential": {"put"},
    "/api/v1/admin/integrations/{provider}/configuration": {"patch"},
    "/api/v1/admin/integrations/{provider}/test": {"post"},
    "/api/v1/admin/integrations/{provider}/disable": {"post"},
    "/api/v1/admin/integrations/{provider}/audit": {"get"},
}

EXPECTED_SCHEMAS = {
    "connection.schema.json",
    "mutations.schema.json",
    "health-check.schema.json",
}
SENSITIVE_FIELD_NAMES = {
    "ciphertext",
    "credential",
    "key_id",
    "last_four",
    "masked",
    "nonce",
    "plaintext",
    "secret",
    "token",
    "value",
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


def test_integration_schemas_are_closed_draft_2020_12_contracts() -> None:
    assert {path.name for path in SCHEMA_ROOT.glob("*.schema.json")} == EXPECTED_SCHEMAS
    for name in EXPECTED_SCHEMAS:
        schema = load_schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


def test_responses_cannot_contain_credential_bearing_fields() -> None:
    for name in EXPECTED_SCHEMAS:
        schema = load_schema(name)
        for definition in schema.get("$defs", {}).values():
            if definition.get("type") != "object":
                continue
            properties = definition.get("properties", {})
            response_properties = {
                field
                for field, field_schema in properties.items()
                if not field_schema.get("writeOnly", False)
            }
            assert not response_properties & SENSITIVE_FIELD_NAMES


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
            assert request_schema["$ref"].startswith("../schemas/integrations/mutations.schema.json#/$defs/")
