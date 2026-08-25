import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "observable_agent_contracts"

SCHEMAS = {
    "fixture_manifest": "agents/fixture-manifest.schema.json",
    "agent_run": "agents/agent-run.schema.json",
    "agent_step": "agents/agent-step.schema.json",
    "model_profile": "agents/model-profile.schema.json",
    "budget_record": "agents/budget-record.schema.json",
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
    with pytest.raises(Exception):
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
        if value.get("type") == "object" or "properties" in value:
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
