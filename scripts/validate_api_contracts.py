import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "civicloop-v1.yaml"
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"


def validate_contracts(openapi_path: Path, schema_root: Path) -> None:
    with openapi_path.open(encoding="utf-8") as source:
        specification = yaml.safe_load(source)

    validate(specification, base_uri=openapi_path.resolve().as_uri())

    schema_paths = sorted(schema_root.rglob("*.schema.json"))
    if not schema_paths:
        raise ValueError(f"No JSON Schemas found below {schema_root}")

    for schema_path in schema_paths:
        with schema_path.open(encoding="utf-8") as source:
            schema = json.load(source)
        Draft202012Validator.check_schema(schema)


def main() -> int:
    validate_contracts(OPENAPI_PATH, SCHEMA_ROOT)
    schema_count = len(list(SCHEMA_ROOT.rglob("*.schema.json")))
    print(f"Validated {OPENAPI_PATH.name} and {schema_count} JSON Schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
