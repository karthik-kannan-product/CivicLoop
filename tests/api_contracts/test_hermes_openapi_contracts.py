import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]


def specification():
    return yaml.safe_load((ROOT / "openapi/civicloop-v1.yaml").read_text(encoding="utf-8"))


def test_hermes_start_accepts_only_explicit_revision_and_session_authority():
    document = specification()
    operation = document["paths"]["/api/v1/workflows/{workflowId}/hermes-runs"]["post"]
    assert document["security"] == [{"sessionCookie": []}]
    assert operation["parameters"] == [
        {"$ref": "#/components/parameters/WorkflowId"},
        {"$ref": "#/components/parameters/CsrfToken"},
        {"$ref": "#/components/parameters/HermesIdempotencyKey"},
    ]
    header = document["components"]["parameters"]["HermesIdempotencyKey"]
    assert header["required"] is True
    assert header["in"] == "header"
    assert header["schema"] == {"type": "string", "format": "uuid", "maxLength": 36}
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator(body)
    validator.validate({"revision_id": 1})
    for rejected in [
        {},
        {"revision_id": 0},
        {"revision_id": True},
        {"revision_id": 1, "actor_slug": "owner"},
        {"revision_id": 1, "capability": "authority"},
    ]:
        with pytest.raises(ValidationError):
            validator.validate(rejected)


def test_hermes_responses_reuse_closed_schemas_and_preserve_legacy_run_reads():
    paths = specification()["paths"]
    status = paths["/api/v1/agent-runs/{runId}"]["get"]
    assert status["responses"]["200"]["content"]["application/json"]["schema"] == {
        "oneOf": [
            {"$ref": "../schemas/agents/hermes-status.schema.json"},
            {"$ref": "../schemas/agents/run-read.schema.json"},
        ]
    }
    for path, method, success, schema in [
        ("/api/v1/workflows/{workflowId}/hermes-runs", "post", "202", "hermes-start"),
        ("/api/v1/agent-runs/{runId}/cancel", "post", "202", "hermes-status"),
        ("/api/v1/agent-runs/{runId}/pending-operations", "get", "200", "pending-operation-page"),
    ]:
        operation = paths[path][method]
        response = operation["responses"][success]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": f"../schemas/agents/{schema}.schema.json"
        }
        assert response["headers"]["Cache-Control"] == {"$ref": "#/components/headers/NoStore"}
        for code in ["401", "403", "404", "409", "503"]:
            assert operation["responses"][code] == {
                "$ref": "#/components/responses/ProblemResponse"
            }

    pending = json.loads((ROOT / "schemas/agents/pending-operation-page.schema.json").read_text())
    results = pending["properties"]["results"]
    assert pending["additionalProperties"] is False
    assert results["maxItems"] == 20
    assert results["items"]["additionalProperties"] is False
    assert results["items"]["properties"]["status"] == {"const": "pending"}
