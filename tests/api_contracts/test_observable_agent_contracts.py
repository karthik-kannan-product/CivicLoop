import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts import validate_api_contracts as contract_validator

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
    "telemetry_metric_record": "agents/telemetry-metric-record.schema.json",
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
    assert str(schema["$id"]).endswith(":v1.0")
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
    normalized_observability = " ".join(observability.split())
    api_contracts = (REPOSITORY_ROOT / "docs" / "api-contracts.md").read_text(encoding="utf-8")
    schema_index = (REPOSITORY_ROOT / "schemas" / "README.md").read_text(encoding="utf-8")
    openapi = (REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml").read_text(encoding="utf-8")

    assert "## Permitted telemetry attributes" in observability
    assert "## Forbidden telemetry attributes" in observability
    for forbidden in [
        "credentials",
        "authentication material",
        "authentication headers",
        "provider credentials",
        "raw personal records",
        "non-synthetic prompt/response bodies",
    ]:
        assert forbidden in normalized_observability
    assert "Schema compatibility" in api_contracts
    assert "schema compatibility" in openapi.lower()
    for relative_path in SCHEMAS.values():
        assert relative_path in schema_index
        assert str(load_json(SCHEMA_ROOT / relative_path)["$id"]) in schema_index


def test_telemetry_export_modes_enforce_privacy_and_keep_workflows_enabled() -> None:
    schema = load_json(SCHEMA_ROOT / "agents/telemetry-export.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    synthetic = load_json(FIXTURE_ROOT / "telemetry_export.valid.json")

    assert synthetic["synthetic_manifest_verified"] is True
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

    unverified_full = deepcopy(synthetic)
    unverified_full["synthetic_manifest_verified"] = False
    with pytest.raises(ValidationError):
        validator.validate(unverified_full)


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


def test_fixture_manifest_digest_uses_the_frozen_canonical_json_projection() -> None:
    payload = load_json(FIXTURE_ROOT / "fixture_manifest.valid.json")
    claimed_digest = payload.pop("manifest_digest")
    canonical_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    api_contracts = (REPOSITORY_ROOT / "docs" / "api-contracts.md").read_text(encoding="utf-8")

    normalized_api_contracts = " ".join(api_contracts.split())
    assert hashlib.sha256(canonical_bytes).hexdigest() == claimed_digest
    assert "RFC 8785 JSON Canonicalization Scheme" in normalized_api_contracts
    assert "omit only `manifest_digest`" in normalized_api_contracts
    assert "exact fixture-file bytes" in normalized_api_contracts
    assert "lowercase hexadecimal" in normalized_api_contracts


@pytest.mark.parametrize(
    ("contract", "terminal_status", "contradictory_field", "contradictory_value"),
    [
        ("agent_run", "succeeded", "failure_category", "timeout"),
        ("agent_run", "failed", "finished_at", None),
        ("agent_run", "failed", "failure_category", "cancelled"),
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


def test_cancelled_runs_and_skipped_steps_have_unambiguous_terminal_timestamps() -> None:
    run_schema = load_json(SCHEMA_ROOT / "agents/agent-run.schema.json")
    run_validator = Draft202012Validator(run_schema, format_checker=FormatChecker())
    cancelled = load_json(FIXTURE_ROOT / "agent_run.valid.json")
    cancelled.update(
        status="cancelled",
        started_at=None,
        finished_at="2026-08-24T12:00:02Z",
        failure_category="cancelled",
    )
    run_validator.validate(cancelled)

    step_schema = load_json(SCHEMA_ROOT / "agents/agent-step.schema.json")
    step_validator = Draft202012Validator(step_schema, format_checker=FormatChecker())
    skipped = load_json(FIXTURE_ROOT / "agent_step.valid.json")
    skipped.update(
        status="skipped",
        started_at=None,
        finished_at="2026-08-24T12:00:02Z",
        failure_category=None,
    )
    step_validator.validate(skipped)

    missing_terminal_timestamp = deepcopy(skipped)
    missing_terminal_timestamp["finished_at"] = None
    with pytest.raises(ValidationError):
        step_validator.validate(missing_terminal_timestamp)

    started_skipped_step = deepcopy(skipped)
    started_skipped_step["started_at"] = "2026-08-24T12:00:01Z"
    with pytest.raises(ValidationError):
        step_validator.validate(started_skipped_step)

    observability = (REPOSITORY_ROOT / "docs" / "observability.md").read_text(encoding="utf-8")
    assert "Every terminal run or step records `finished_at`" in observability
    assert "A skipped step never starts" in observability
    assert "cancelled before or after it starts" in observability


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
    assert "allOf" not in schema
    assert len(schema["oneOf"]) == 3
    assert "oneOf" not in properties["judge"]
    assert '"kind"' not in json.dumps(schema["oneOf"])


def test_evaluation_judge_modes_have_one_profile_reference_and_exclusive_configuration() -> None:
    schema = load_json(SCHEMA_ROOT / "evaluations/result.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    deterministic = load_json(FIXTURE_ROOT / "evaluation_result.valid.json")
    api_contracts = (REPOSITORY_ROOT / "docs" / "api-contracts.md").read_text(encoding="utf-8")

    validator.validate(deterministic)

    llm_judge = deepcopy(deterministic)
    llm_judge.update(
        evaluator="llm_judge",
        evaluator_profile_id="hermes_judge",
        evaluator_profile_revision=1,
    )
    llm_judge["judge"] = {"config_id": "strict_v1"}
    validator.validate(llm_judge)

    human_review = deepcopy(deterministic)
    human_review.update(evaluator="human_review", evaluator_profile_id=None)
    human_review["judge"] = {
        "reviewer_id": "reviewer_001",
        "review_policy_id": "review_policy_v1",
    }
    validator.validate(human_review)

    contradictions = []
    legacy_kind = deepcopy(deterministic)
    legacy_kind["judge"]["kind"] = "deterministic"
    contradictions.append(legacy_kind)
    deterministic_config = deepcopy(deterministic)
    deterministic_config["judge"]["config_id"] = "strict_v1"
    contradictions.append(deterministic_config)
    llm_duplicate_profile = deepcopy(llm_judge)
    llm_duplicate_profile["judge"]["profile_id"] = "hermes_judge"
    contradictions.append(llm_duplicate_profile)
    llm_missing_config = deepcopy(llm_judge)
    llm_missing_config["judge"] = {}
    contradictions.append(llm_missing_config)
    llm_reviewer = deepcopy(llm_judge)
    llm_reviewer["judge"]["reviewer_id"] = "reviewer_001"
    contradictions.append(llm_reviewer)
    human_missing_reviewer = deepcopy(human_review)
    human_missing_reviewer["judge"].pop("reviewer_id")
    contradictions.append(human_missing_reviewer)
    human_config = deepcopy(human_review)
    human_config["judge"]["config_id"] = "strict_v1"
    contradictions.append(human_config)

    for contradiction in contradictions:
        with pytest.raises(ValidationError):
            validator.validate(contradiction)

    assert (
        "`evaluator_profile_id` plus `evaluator_profile_revision` is the sole "
        "immutable model-profile coordinate"
    ) in " ".join(api_contracts.split())


def test_immutable_provenance_coordinates_support_reused_logical_ids() -> None:
    run_schema = load_json(SCHEMA_ROOT / "agents/agent-run.schema.json")
    run_validator = Draft202012Validator(run_schema, format_checker=FormatChecker())
    run_v1 = load_json(FIXTURE_ROOT / "agent_run.valid.json")

    run_validator.validate(run_v1)
    manifest = load_json(FIXTURE_ROOT / "fixture_manifest.valid.json")
    export = load_json(FIXTURE_ROOT / "telemetry_export.valid.json")
    example = load_json(FIXTURE_ROOT / "evaluation_example.valid.json")
    manifest_coordinate = (
        manifest["manifest_id"],
        manifest["revision"],
        manifest["manifest_digest"],
    )
    assert manifest_coordinate == (
        run_v1["fixture_manifest_id"],
        run_v1["fixture_manifest_revision"],
        run_v1["fixture_manifest_digest"],
    )
    assert manifest_coordinate == (
        export["fixture_manifest_id"],
        export["fixture_manifest_revision"],
        export["fixture_manifest_digest"],
    )
    assert manifest_coordinate == (
        example["fixture_manifest_id"],
        example["fixture_revision"],
        example["fixture_manifest_digest"],
    )
    run_v2 = deepcopy(run_v1)
    run_v2["fixture_manifest_revision"] = 2
    run_v2["fixture_manifest_digest"] = "b" * 64
    run_v2["model_profile_revision"] = 2
    run_validator.validate(run_v2)
    assert run_v1["fixture_manifest_id"] == run_v2["fixture_manifest_id"]
    assert run_v1["model_profile_id"] == run_v2["model_profile_id"]

    export_v2 = deepcopy(export)
    export_v2["fixture_manifest_revision"] = 2
    export_v2["fixture_manifest_digest"] = "b" * 64
    Draft202012Validator(
        load_json(SCHEMA_ROOT / SCHEMAS["telemetry_export"]),
        format_checker=FormatChecker(),
    ).validate(export_v2)
    example_v2 = deepcopy(example)
    example_v2["fixture_revision"] = 2
    example_v2["fixture_manifest_digest"] = "b" * 64
    Draft202012Validator(
        load_json(SCHEMA_ROOT / SCHEMAS["evaluation_example"]),
        format_checker=FormatChecker(),
    ).validate(example_v2)

    for field in [
        "fixture_manifest_revision",
        "fixture_manifest_digest",
        "model_profile_revision",
    ]:
        incomplete = deepcopy(run_v1)
        incomplete.pop(field)
        with pytest.raises(ValidationError):
            run_validator.validate(incomplete)

    for contract, revision_field in [
        ("budget_record", "model_profile_revision"),
        ("evaluation_result", "evaluator_profile_revision"),
    ]:
        schema = load_json(SCHEMA_ROOT / SCHEMAS[contract])
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        payload = load_json(FIXTURE_ROOT / f"{contract}.valid.json")
        assert revision_field in payload
        validator.validate(payload)

    budget = load_json(FIXTURE_ROOT / "budget_record.valid.json")
    assert (budget["model_profile_id"], budget["model_profile_revision"]) == (
        run_v1["model_profile_id"],
        run_v1["model_profile_revision"],
    )
    budget_v2 = deepcopy(budget)
    budget_v2["model_profile_revision"] = 2
    Draft202012Validator(
        load_json(SCHEMA_ROOT / SCHEMAS["budget_record"]),
        format_checker=FormatChecker(),
    ).validate(budget_v2)
    assert budget_v2["model_profile_id"] == budget["model_profile_id"]


def test_synthetic_full_export_is_anchored_to_run_and_manifest_coordinates() -> None:
    schema = load_json(SCHEMA_ROOT / "agents/telemetry-export.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    export = load_json(FIXTURE_ROOT / "telemetry_export.valid.json")

    assert "synthetic_run" not in export
    for field in [
        "run_id",
        "fixture_manifest_id",
        "fixture_manifest_revision",
        "fixture_manifest_digest",
        "synthetic_manifest_verified",
    ]:
        assert field in export
    validator.validate(export)

    missing_anchor = deepcopy(export)
    missing_anchor.pop("fixture_manifest_digest")
    with pytest.raises(ValidationError):
        validator.validate(missing_anchor)

    unverified = deepcopy(export)
    unverified["synthetic_manifest_verified"] = False
    with pytest.raises(ValidationError):
        validator.validate(unverified)


def test_evaluation_result_has_one_canonical_example_identity() -> None:
    schema = load_json(SCHEMA_ROOT / "evaluations/result.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    result = load_json(FIXTURE_ROOT / "evaluation_result.valid.json")

    assert "example_id" not in result
    validator.validate(result)
    duplicated = deepcopy(result)
    duplicated["example_id"] = duplicated["example"]["example_id"]
    with pytest.raises(ValidationError):
        validator.validate(duplicated)


def test_evaluation_references_are_bounded_opaque_ids_not_paths_or_content() -> None:
    example_schema = load_json(SCHEMA_ROOT / "evaluations/example.schema.json")
    example_validator = Draft202012Validator(example_schema, format_checker=FormatChecker())
    example = load_json(FIXTURE_ROOT / "evaluation_example.valid.json")
    example_validator.validate(example)

    for forbidden_reference in ["../private/member", "fixtures/events/member.json", "raw prompt"]:
        invalid = deepcopy(example)
        invalid["input_reference"] = forbidden_reference
        with pytest.raises(ValidationError):
            example_validator.validate(invalid)

    result_schema = load_json(SCHEMA_ROOT / "evaluations/result.schema.json")
    result_validator = Draft202012Validator(result_schema, format_checker=FormatChecker())
    result = load_json(FIXTURE_ROOT / "evaluation_result.valid.json")
    for forbidden_reference in ["../private/prompt", "prompts/system.txt", "raw prompt"]:
        invalid = deepcopy(result)
        invalid["prompt"]["reference"] = forbidden_reference
        with pytest.raises(ValidationError):
            result_validator.validate(invalid)


def test_metric_variants_enforce_value_types_outcomes_and_relevant_labels() -> None:
    schema = load_json(SCHEMA_ROOT / "agents/telemetry-metric-record.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert set(schema["$defs"]) == {"model_profile_id", "model_profile_revision"}
    for variant in schema["oneOf"][:3]:
        label_properties = variant["properties"]["labels"]["properties"]
        assert label_properties["model_profile_id"] == {"$ref": "#/$defs/model_profile_id"}
        assert label_properties["model_profile_revision"] == {
            "$ref": "#/$defs/model_profile_revision"
        }

    token_count = {
        "schema_version": "1.0",
        "metric": "agent_tokens",
        "value": 42,
        "labels": {
            "capability_profile": "workflow",
            "model_profile_id": "hermes_primary",
            "model_profile_revision": 1,
            "token_direction": "input",
        },
    }
    validator.validate(token_count)
    fractional = deepcopy(token_count)
    fractional["value"] = 1.5
    with pytest.raises(ValidationError):
        validator.validate(fractional)

    cost_count = deepcopy(token_count)
    cost_count.update(metric="agent_cost_microusd", value=1250)
    cost_count["labels"].pop("token_direction")
    validator.validate(cost_count)
    fractional_cost = deepcopy(cost_count)
    fractional_cost["value"] = 1.5
    with pytest.raises(ValidationError):
        validator.validate(fractional_cost)

    evaluation_outcome = {
        "schema_version": "1.0",
        "metric": "evaluation_outcome",
        "outcome": "passed",
        "labels": {"evaluation_label": "expected_output"},
    }
    validator.validate(evaluation_outcome)
    missing_outcome = deepcopy(evaluation_outcome)
    missing_outcome.pop("outcome")
    with pytest.raises(ValidationError):
        validator.validate(missing_outcome)

    missing_profile_revision = deepcopy(token_count)
    missing_profile_revision["labels"].pop("model_profile_revision")
    with pytest.raises(ValidationError):
        validator.validate(missing_profile_revision)

    crossed_provider_model = load_json(FIXTURE_ROOT / "telemetry_metric_record.valid.json")
    crossed_provider_model["labels"].update(provider="openai", model="hermes-3")
    with pytest.raises(ValidationError):
        validator.validate(crossed_provider_model)

    invalid_label = deepcopy(token_count)
    invalid_label["labels"]["approval_state"] = "approved"
    with pytest.raises(ValidationError):
        validator.validate(invalid_label)


def test_evaluation_result_negative_fixture_only_changes_created_at() -> None:
    valid = load_json(FIXTURE_ROOT / "evaluation_result.valid.json")
    invalid = load_json(FIXTURE_ROOT / "evaluation_result.invalid.json")

    assert invalid.pop("created_at") == "2026-08-24"
    valid.pop("created_at")
    assert invalid == valid


def test_telemetry_export_must_match_its_anchored_run() -> None:
    run = load_json(FIXTURE_ROOT / "agent_run.valid.json")
    export = load_json(FIXTURE_ROOT / "telemetry_export.valid.json")

    contract_validator.validate_telemetry_export_against_run(run, export)

    mismatches = {
        "privacy_mode": (run, "privacy_mode", "pilot_minimized"),
        "manifest revision": (export, "fixture_manifest_revision", 2),
        "manifest digest": (export, "fixture_manifest_digest", "b" * 64),
    }
    for expected_error, (source, field, value) in mismatches.items():
        mismatched_run = deepcopy(run)
        mismatched_export = deepcopy(export)
        target = mismatched_run if source is run else mismatched_export
        target[field] = value
        with pytest.raises(ValueError, match=expected_error):
            contract_validator.validate_telemetry_export_against_run(
                mismatched_run, mismatched_export
            )
