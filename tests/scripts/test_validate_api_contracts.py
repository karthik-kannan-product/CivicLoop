from pathlib import Path

import pytest
from jsonschema.exceptions import SchemaError

from scripts.validate_api_contracts import (
    OPENAPI_PATH,
    SCHEMA_ROOT,
    validate_contracts,
)


def test_checked_in_openapi_and_json_schemas_are_valid() -> None:
    validate_contracts(OPENAPI_PATH, SCHEMA_ROOT)


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

    with pytest.raises(SchemaError):
        validate_contracts(openapi_path, schema_root)
