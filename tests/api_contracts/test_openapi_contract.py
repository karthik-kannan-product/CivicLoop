from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"


def _load_openapi() -> dict[str, object]:
    with OPENAPI_PATH.open(encoding="utf-8") as source:
        return yaml.safe_load(source)


def test_openapi_indexes_internal_hermes_and_draft_operation_contracts() -> None:
    document = _load_openapi()
    schemas = document["components"]["schemas"]

    assert schemas == {
        "DraftOperation": {
            "$ref": "../schemas/integrations/draft-operation.schema.json"
        },
        "HermesRunRequest": {
            "$ref": "../schemas/agents/hermes-run-request.schema.json"
        },
        "HermesRunResult": {
            "$ref": "../schemas/agents/hermes-run-result.schema.json"
        },
        "ModelGatewayProfile": {
            "$ref": "../schemas/agents/model-gateway-profile.schema.json"
        },
        "WorkflowCapability": {
            "$ref": "../schemas/integrations/workflow-capability.schema.json"
        },
    }


def test_internal_agent_endpoints_are_service_authenticated_and_schema_bound() -> None:
    document = _load_openapi()
    paths = document["paths"]

    run = paths["/internal/v1/hermes/runs"]["post"]
    assert run["security"] == [{"internalServiceToken": []}]
    assert run["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HermesRunRequest"
    }
    assert run["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HermesRunResult"
    }

    operation = paths["/internal/v1/draft-operations"]["post"]
    assert operation["security"] == [{"internalServiceToken": []}]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DraftOperation"
    }
    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DraftOperation"
    }


def test_internal_service_token_is_not_a_session_or_query_credential() -> None:
    scheme = _load_openapi()["components"]["securitySchemes"]["internalServiceToken"]
    assert scheme == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque-service-token",
        "description": "Internal-only token loaded from a mode-0600 file; never logged or traced.",
    }
