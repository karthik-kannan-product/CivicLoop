import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"
CONTRACT_FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "api_contracts" / "fixtures"
OBSERVABLE_FIXTURE_SCHEMAS = {
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


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_non_finite_constant(constant: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {constant}")


class UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def construct_unique_mapping(
    loader: UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            source_name = getattr(loader.stream, "name", "<stream>")
            raise ValueError(f"{source_name}: Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


def load_json_contract(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding="utf-8") as source:
            document = json.load(
                source,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_non_finite_constant,
            )
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{path}: Contract JSON must contain a top-level object")
    return document


def load_yaml_contract(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as source:
        document = yaml.load(source, Loader=UniqueKeySafeLoader)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: Contract YAML must contain a top-level mapping")
    return document


def validate_contracts(
    openapi_path: Path,
    schema_root: Path,
    fixture_root: Path | None,
    *,
    allow_partial_observable_fixtures: bool = False,
) -> None:
    if fixture_root is None:
        raise ValueError("fixture_root is required; fixture validation cannot be disabled")

    specification = load_yaml_contract(openapi_path)

    validate(specification, base_uri=openapi_path.resolve().as_uri())

    schema_paths = sorted(schema_root.rglob("*.schema.json"))
    if not schema_paths:
        raise ValueError(f"No JSON Schemas found below {schema_root}")

    for schema_path in schema_paths:
        schema = load_json_contract(schema_path)
        Draft202012Validator.check_schema(schema)

    if fixture_root is not None:
        for fixture_path in sorted(fixture_root.rglob("*.json")):
            load_json_contract(fixture_path)

        validate_observable_fixtures(
            schema_root,
            fixture_root,
            allow_partial=allow_partial_observable_fixtures,
        )


def main() -> int:
    validate_contracts(OPENAPI_PATH, SCHEMA_ROOT, CONTRACT_FIXTURE_ROOT)
    schema_count = len(list(SCHEMA_ROOT.rglob("*.schema.json")))
    print(f"Validated {OPENAPI_PATH.name} and {schema_count} JSON Schemas.")
    return 0


def validate_telemetry_export_against_run(
    run: dict[str, object], export: dict[str, object]
) -> None:
    comparisons = {
        "run_id": (run.get("run_id"), export.get("run_id")),
        "privacy_mode": (run.get("privacy_mode"), export.get("privacy_mode")),
        "manifest ID": (
            run.get("fixture_manifest_id"),
            export.get("fixture_manifest_id"),
        ),
        "manifest revision": (
            run.get("fixture_manifest_revision"),
            export.get("fixture_manifest_revision"),
        ),
        "manifest digest": (
            run.get("fixture_manifest_digest"),
            export.get("fixture_manifest_digest"),
        ),
    }
    for coordinate, (run_value, export_value) in comparisons.items():
        if run_value != export_value:
            raise ValueError(f"Telemetry export {coordinate} does not match its anchored agent run")


def validate_observable_fixtures(
    schema_root: Path, fixture_root: Path, *, allow_partial: bool = False
) -> None:
    observable_root = fixture_root / "observable_agent_contracts"
    if not observable_root.is_dir():
        if allow_partial:
            return
        raise ValueError(f"Missing observable fixture root: {observable_root}")

    loaded_valid_fixtures: dict[str, dict[str, object]] = {}
    for contract, relative_schema_path in OBSERVABLE_FIXTURE_SCHEMAS.items():
        valid_path = observable_root / f"{contract}.valid.json"
        invalid_path = observable_root / f"{contract}.invalid.json"
        if valid_path.exists() != invalid_path.exists():
            raise ValueError(f"Incomplete observable fixture pair: {contract}")
        if not valid_path.exists():
            if allow_partial:
                continue
            raise ValueError(f"Missing observable fixture pair: {contract}")

        schema = load_json_contract(schema_root / relative_schema_path)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        valid_fixture = load_json_contract(valid_path)
        loaded_valid_fixtures[contract] = valid_fixture
        validator.validate(valid_fixture)
        if validator.is_valid(load_json_contract(invalid_path)):
            raise ValueError(f"{invalid_path}: Negative contract fixture unexpectedly validates")

    if {"agent_run", "telemetry_export"} <= loaded_valid_fixtures.keys():
        validate_telemetry_export_against_run(
            loaded_valid_fixtures["agent_run"],
            loaded_valid_fixtures["telemetry_export"],
        )


if __name__ == "__main__":
    raise SystemExit(main())
