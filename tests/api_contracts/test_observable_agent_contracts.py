import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "observable_agent_contracts"

SCHEMAS = {
    "fixture_manifest": "agents/fixture-manifest.schema.json",
    "agent_run": "agents/agent-run.schema.json",
    "agent_step": "agents/agent-step.schema.json",
    "model_profile": "agents/model-profile.schema.json",
    "budget_record": "agents/budget-record.schema.json",
    "telemetry_export": "agents/telemetry-export.schema.json",
    "evaluation_example": "evaluations/example.schema.json",
    "evaluation_result": "evaluations/result.schema.json",
}


def load_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


@pytest.mark.parametrize("contract,relative_path", SCHEMAS.items())
def test_observable_agent_contracts_validate_positive_and_reject_negative_fixtures(
    contract: str, relative_path: str
) -> None:
    schema = load_json(SCHEMA_ROOT / relative_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    validator.validate(load_json(FIXTURE_ROOT / f"{contract}.valid.json"))
    with pytest.raises(ValidationError):
        validator.validate(load_json(FIXTURE_ROOT / f"{contract}.invalid.json"))


@pytest.mark.parametrize("relative_path", SCHEMAS.values())
def test_observable_agent_contracts_are_closed_versioned_draft_2020_12_schemas(
    relative_path: str,
) -> None:
    schema = load_json(SCHEMA_ROOT / relative_path)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert str(schema["$id"]).startswith("urn:civicloop:schema:")
    assert ":v1" in str(schema["$id"])
    _assert_all_object_shapes_are_closed(schema)


def _assert_all_object_shapes_are_closed(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for nested in value.values():
            _assert_all_object_shapes_are_closed(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_all_object_shapes_are_closed(nested)


def test_observability_policy_and_contract_indexes_define_telemetry_and_compatibility() -> None:
    observability = (REPOSITORY_ROOT / "docs" / "observability.md").read_text(encoding="utf-8")
    api_contracts = (REPOSITORY_ROOT / "docs" / "api-contracts.md").read_text(
        encoding="utf-8"
    )
    openapi = (REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml").read_text(encoding="utf-8")

    assert "## Permitted telemetry attributes" in observability
    assert "## Forbidden telemetry attributes" in observability
    for forbidden in [
        "credentials",
        "authentication material",
        "raw personal records",
        "non-synthetic prompt/response bodies",
    ]:
        assert forbidden in observability
    assert "Schema compatibility" in api_contracts
    assert "schema compatibility" in openapi.lower()


def test_telemetry_export_modes_enforce_privacy_and_keep_workflows_enabled() -> None:
    schema = load_json(SCHEMA_ROOT / "agents/telemetry-export.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    synthetic = load_json(FIXTURE_ROOT / "telemetry_export.valid.json")

    validator.validate(synthetic)
    for mode, export_enabled, prompt_content, tool_content in [
        ("pilot_minimized", True, "redacted", "redacted"),
        ("disabled", False, "omitted", "omitted"),
    ]:
        payload = deepcopy(synthetic)
        payload.update(
            privacy_mode=mode,
            export_enabled=export_enabled,
            prompt_response_content=prompt_content,
            tool_payload_content=tool_content,
        )
        validator.validate(payload)

    contradictory = deepcopy(synthetic)
    contradictory["privacy_mode"] = "disabled"
    with pytest.raises(ValidationError):
        validator.validate(contradictory)


def test_fixture_manifest_prevents_path_traversal_and_uses_a_digest_and_unique_id_map() -> None:
    schema = load_json(SCHEMA_ROOT / "agents/fixture-manifest.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    payload = load_json(FIXTURE_ROOT / "fixture_manifest.valid.json")

    assert schema["properties"]["fixtures"]["type"] == "object"
    assert "manifest_digest" in schema["required"]
    traversal = deepcopy(payload)
    traversal["fixtures"]["ny_youth_day"]["path"] = "../private/event.json"
    with pytest.raises(ValidationError):
        validator.validate(traversal)


@pytest.mark.parametrize(
    ("contract", "terminal_status", "contradictory_field", "contradictory_value"),
    [
        ("agent_run", "succeeded", "failure_category", "timeout"),
        ("agent_run", "failed", "finished_at", None),
        ("agent_step", "succeeded", "failure_category", "timeout"),
        ("agent_step", "failed", "finished_at", None),
    ],
)
def test_agent_lifecycle_contracts_reject_contradictory_terminal_records(
    contract: str, terminal_status: str, contradictory_field: str, contradictory_value: object
) -> None:
    schema = load_json(SCHEMA_ROOT / SCHEMAS[contract])
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    payload = load_json(FIXTURE_ROOT / f"{contract}.valid.json")
    payload["status"] = terminal_status
    payload["started_at"] = "2026-08-24T12:00:01Z"
    payload["finished_at"] = "2026-08-24T12:00:02Z"
    payload["failure_category"] = None if terminal_status == "succeeded" else "timeout"
    payload[contradictory_field] = contradictory_value

    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_evaluation_result_is_the_canonical_cross_tool_advisory_record() -> None:
    schema = load_json(SCHEMA_ROOT / "evaluations/result.schema.json")
    properties = schema["properties"]

    assert set(properties) >= {
        "dataset",
        "example",
        "prompt",
        "policy",
        "evaluated_schema",
        "candidate",
        "judge",
        "rubric",
        "deterministic_checks",
        "usage",
        "latency_ms",
        "trace_id",
        "advisory_only",
    }
    assert properties["advisory_only"] == {"const": True}