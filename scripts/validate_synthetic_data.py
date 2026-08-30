import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "loops" / "launchloop" / "data" / "manifest.json"
SCHEMA_PATH = REPOSITORY_ROOT / "schemas" / "agents" / "fixture-manifest.schema.json"
METADATA_FIXTURE_ID = "launchloop_fixture_metadata"
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PROHIBITED_KEYS = {
    "access_token",
    "api_key",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "totp",
}
TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class SyntheticDataSummary:
    manifest_id: str
    revision: int
    fixture_count: int
    event_count: int
    case_count: int


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(constant: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {constant}")


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(
                source,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def _canonical_manifest_digest(manifest: dict[str, Any]) -> str:
    projection = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    canonical = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _discover_fixture_paths(repository_root: Path) -> set[str]:
    launchloop_root = repository_root / "loops" / "launchloop"
    discovered = {
        path.relative_to(repository_root).as_posix()
        for path in (launchloop_root / "data").glob("*.json")
        if path.name != "manifest.json"
    }
    discovered.update(
        path.relative_to(repository_root).as_posix()
        for path in (launchloop_root / "policies").glob("*.md")
    )
    evaluation_cases = launchloop_root / "eval_cases.json"
    if evaluation_cases.exists():
        discovered.add(evaluation_cases.relative_to(repository_root).as_posix())
    return discovered


def _scan_string(value: str, source: Path) -> None:
    for pattern in TOKEN_PATTERNS:
        if pattern.search(value):
            raise ValueError(f"Prohibited credential-like value in {source}")
    for match in EMAIL_PATTERN.finditer(value):
        if match.group(1).lower() != "example.test":
            raise ValueError(f"Prohibited non-synthetic email domain in {source}")


def _scan_json(value: Any, source: Path) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = key.lower()
            if any(
                normalized_key == prohibited or normalized_key.endswith(f"_{prohibited}")
                for prohibited in PROHIBITED_KEYS
            ):
                raise ValueError(f"Prohibited credential field {key!r} in {source}")
            _scan_json(item, source)
    elif isinstance(value, list):
        for item in value:
            _scan_json(item, source)
    elif isinstance(value, str):
        _scan_string(value, source)


def _require_unique_ids(records: list[Any], field: str, source: Path) -> set[str]:
    identifiers: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(field), str):
            raise ValueError(f"Every record in {source} must have a string {field}")
        identifier = record[field]
        if identifier in identifiers:
            raise ValueError(f"Duplicate {field}: {identifier}")
        identifiers.add(identifier)
    return identifiers


def _validate_metadata(metadata: Any, manifest_ids: set[str], source: Path) -> None:
    if not isinstance(metadata, dict):
        raise ValueError(f"{source}: metadata must be an object")
    if set(metadata) != {"schema_version", "synthetic", "provenance", "fixtures"}:
        raise ValueError(f"{source}: metadata has unexpected or missing fields")
    if metadata["schema_version"] != "1.0" or metadata["synthetic"] is not True:
        raise ValueError(f"{source}: metadata must be version 1.0 and synthetic")
    if metadata["provenance"] != "hand_authored_synthetic":
        raise ValueError(f"{source}: unsupported provenance")

    fixtures = metadata["fixtures"]
    if not isinstance(fixtures, dict) or set(fixtures) != manifest_ids:
        raise ValueError("Manifest and metadata fixture IDs differ")
    for fixture_id, fixture_metadata in fixtures.items():
        if not isinstance(fixture_metadata, dict) or set(fixture_metadata) != {
            "scenario_tags",
            "prd_risks",
        }:
            raise ValueError(f"Invalid metadata for fixture {fixture_id}")
        for field in ("scenario_tags", "prd_risks"):
            tags = fixture_metadata[field]
            if (
                not isinstance(tags, list)
                or not tags
                or len(tags) != len(set(tags))
                or any(not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag) for tag in tags)
            ):
                raise ValueError(f"Invalid {field} for fixture {fixture_id}")


def validate_synthetic_data(
    repository_root: Path = REPOSITORY_ROOT,
    manifest_path: Path = MANIFEST_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> SyntheticDataSummary:
    repository_root = repository_root.resolve()
    manifest = _load_json(manifest_path)
    schema = _load_json(schema_path)
    if not isinstance(manifest, dict) or not isinstance(schema, dict):
        raise ValueError("Manifest and schema must be JSON objects")
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    expected_digest = _canonical_manifest_digest(manifest)
    if manifest["manifest_digest"] != expected_digest:
        raise ValueError("Manifest digest does not match its canonical projection")

    fixtures = manifest["fixtures"]
    manifest_paths = {entry["path"] for entry in fixtures.values()}
    if len(manifest_paths) != len(fixtures):
        raise ValueError("Multiple fixture IDs cannot reference the same path")

    loaded_json: dict[str, Any] = {}
    resolved_paths: dict[str, Path] = {}
    for fixture_id, entry in fixtures.items():
        fixture_path = (repository_root / entry["path"]).resolve()
        if repository_root not in fixture_path.parents:
            raise ValueError(f"Fixture path escapes repository root: {entry['path']}")
        if not fixture_path.is_file():
            raise ValueError(f"Missing fixture file: {entry['path']}")
        resolved_paths[fixture_id] = fixture_path
        if fixture_path.suffix == ".json":
            document = _load_json(fixture_path)
            loaded_json[fixture_id] = document
            _scan_json(document, fixture_path)
        else:
            _scan_string(fixture_path.read_text(encoding="utf-8"), fixture_path)

    discovered_paths = _discover_fixture_paths(repository_root)
    unlisted_paths = sorted(discovered_paths - manifest_paths)
    if unlisted_paths:
        raise ValueError(f"Unlisted fixture file: {unlisted_paths[0]}")
    missing_inventory_paths = sorted(manifest_paths - discovered_paths)
    if missing_inventory_paths:
        missing_path = missing_inventory_paths[0]
        raise ValueError(f"Manifest path is outside the fixture inventory: {missing_path}")

    _validate_metadata(
        loaded_json.get(METADATA_FIXTURE_ID),
        set(fixtures),
        resolved_paths[METADATA_FIXTURE_ID],
    )

    events = loaded_json.get("launchloop_events")
    cases = loaded_json.get("launchloop_evaluation_cases")
    if not isinstance(events, list) or not isinstance(cases, list):
        raise ValueError("Event and evaluation fixtures must be JSON arrays")
    event_ids = _require_unique_ids(events, "event_id", resolved_paths["launchloop_events"])
    _require_unique_ids(cases, "case_id", resolved_paths["launchloop_evaluation_cases"])
    for case in cases:
        if case["event_id"] not in event_ids:
            raise ValueError(f"Evaluation case references unknown event ID: {case['event_id']}")

    for fixture_id, entry in fixtures.items():
        actual_hash = hashlib.sha256(resolved_paths[fixture_id].read_bytes()).hexdigest()
        if actual_hash != entry["sha256"]:
            raise ValueError(f"Checksum mismatch for {entry['path']}")

    return SyntheticDataSummary(
        manifest_id=manifest["manifest_id"],
        revision=manifest["revision"],
        fixture_count=len(fixtures),
        event_count=len(events),
        case_count=len(cases),
    )


def main() -> int:
    summary = validate_synthetic_data()
    print(
        f"Validated {summary.fixture_count} synthetic fixtures, "
        f"{summary.event_count} events, and {summary.case_count} evaluation cases "
        f"for {summary.manifest_id} revision {summary.revision}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
