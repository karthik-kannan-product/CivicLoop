from pathlib import Path

import pytest
from jsonschema.exceptions import SchemaError, ValidationError

from scripts.validate_api_contracts import (
    CONTRACT_FIXTURE_ROOT,
    OPENAPI_PATH,
    SCHEMA_ROOT,
    validate_contracts,
)


def test_checked_in_openapi_and_json_schemas_are_valid() -> None:
    validate_contracts(OPENAPI_PATH, SCHEMA_ROOT, CONTRACT_FIXTURE_ROOT)


def test_invalid_json_schema_is_rejected(tmp_path: Path) -> None:
    openapi_path = tmp_path / "openapi.yaml"
    schema_root = tmp_path / "schemas"
    schema_root.mkdir()
    openapi_path.write_text(
        """
openapi: 3.1.1
info:
  title: Contract test
  version: 1.0.0
paths: {}
""".strip(),
        encoding="utf-8",
    )
    (schema_root / "invalid.schema.json").write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":7}',
        encoding="utf-8",
    )

    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    with pytest.raises(SchemaError):
        validate_contracts(openapi_path, schema_root, fixture_root)


def _write_minimal_contract_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    openapi_path = tmp_path / "openapi.yaml"
    schema_root = tmp_path / "schemas"
    fixture_root = tmp_path / "fixtures"
    schema_root.mkdir()
    fixture_root.mkdir()
    openapi_path.write_text(
        "openapi: 3.1.1\ninfo: {title: Contract test, version: 1.0.0}\npaths: {}\n",
        encoding="utf-8",
    )
    (schema_root / "valid.schema.json").write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}',
        encoding="utf-8",
    )
    return openapi_path, schema_root, fixture_root


def test_duplicate_keys_are_rejected_in_json_schemas(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    schema_path = schema_root / "duplicate.schema.json"
    schema_path.write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        '"type":"object","type":"string"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"duplicate\.schema\.json.*Duplicate JSON key: type"):
        validate_contracts(openapi_path, schema_root, fixture_root)


def test_duplicate_keys_are_rejected_in_json_contract_fixtures(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    fixture_path = fixture_root / "duplicate.json"
    fixture_path.write_text('{"status":"passed","status":"failed"}', encoding="utf-8")

    with pytest.raises(ValueError, match=r"duplicate\.json.*Duplicate JSON key: status"):
        validate_contracts(openapi_path, schema_root, fixture_root)


def test_duplicate_keys_are_rejected_in_openapi_yaml(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    openapi_path.write_text(
        """
openapi: 3.1.1
info:
  title: Contract test
  version: 1.0.0
paths: {}
paths: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"openapi\.yaml.*Duplicate YAML key: paths"):
        validate_contracts(openapi_path, schema_root, fixture_root)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_constants_are_rejected_in_json_schemas(tmp_path: Path, constant: str) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    (schema_root / "non-finite.schema.json").write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        f'"type":"object","x-test":{constant}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"Non-finite JSON constant: {constant}"):
        validate_contracts(openapi_path, schema_root, fixture_root)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_constants_are_rejected_in_json_contract_fixtures(
    tmp_path: Path, constant: str
) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    (fixture_root / "non-finite.json").write_text(f'{{"value":{constant}}}', encoding="utf-8")

    with pytest.raises(ValueError, match=rf"Non-finite JSON constant: {constant}"):
        validate_contracts(openapi_path, schema_root, fixture_root)


def test_observable_fixture_validation_asserts_rfc3339_formats(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    evaluation_root = schema_root / "evaluations"
    evaluation_root.mkdir()
    (evaluation_root / "result.schema.json").write_text(
        """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["created_at"],
  "properties": {"created_at": {"type": "string", "format": "date-time"}}
}""",
        encoding="utf-8",
    )
    observable_root = fixture_root / "observable_agent_contracts"
    observable_root.mkdir()
    (observable_root / "evaluation_result.valid.json").write_text(
        '{"created_at":"2026-08-24"}', encoding="utf-8"
    )
    (observable_root / "evaluation_result.invalid.json").write_text(
        '{"created_at":"also-not-rfc3339"}', encoding="utf-8"
    )

    with pytest.raises(ValidationError):
        validate_contracts(
            openapi_path,
            schema_root,
            fixture_root,
            allow_partial_observable_fixtures=True,
        )


def test_observable_fixture_root_is_required_by_default(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)

    with pytest.raises(ValueError, match="Missing observable fixture root"):
        validate_contracts(openapi_path, schema_root, fixture_root)


def test_every_mapped_observable_fixture_pair_is_required(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    (fixture_root / "observable_agent_contracts").mkdir()

    with pytest.raises(ValueError, match="Missing observable fixture pair: fixture_manifest"):
        validate_contracts(openapi_path, schema_root, fixture_root)


def test_observable_fixture_pair_cannot_have_only_one_member(tmp_path: Path) -> None:
    openapi_path, schema_root, fixture_root = _write_minimal_contract_roots(tmp_path)
    observable_root = fixture_root / "observable_agent_contracts"
    observable_root.mkdir()
    (observable_root / "fixture_manifest.valid.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Incomplete observable fixture pair: fixture_manifest"):
        validate_contracts(
            openapi_path,
            schema_root,
            fixture_root,
            allow_partial_observable_fixtures=True,
        )


def test_fixture_validation_cannot_be_omitted(tmp_path: Path) -> None:
    openapi_path, schema_root, _fixture_root = _write_minimal_contract_roots(tmp_path)

    with pytest.raises(TypeError):
        validate_contracts(openapi_path, schema_root)


def test_fixture_validation_cannot_be_disabled_with_none(tmp_path: Path) -> None:
    openapi_path, schema_root, _fixture_root = _write_minimal_contract_roots(tmp_path)

    with pytest.raises(ValueError, match="fixture_root is required"):
        validate_contracts(openapi_path, schema_root, None)
