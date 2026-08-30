import json
import shutil
from pathlib import Path

import pytest

from scripts.validate_synthetic_data import (
    MANIFEST_PATH,
    REPOSITORY_ROOT,
    SCHEMA_PATH,
    validate_synthetic_data,
)


def _copy_fixture_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository_root = tmp_path / "repository"
    source_loop = REPOSITORY_ROOT / "loops" / "launchloop"
    target_loop = repository_root / "loops" / "launchloop"
    shutil.copytree(source_loop, target_loop)
    schema_path = repository_root / "schemas" / "agents" / "fixture-manifest.schema.json"
    schema_path.parent.mkdir(parents=True)
    shutil.copy2(SCHEMA_PATH, schema_path)
    manifest_path = repository_root / "loops" / "launchloop" / "data" / "manifest.json"
    return repository_root, manifest_path, schema_path


def test_checked_in_synthetic_manifest_validates() -> None:
    summary = validate_synthetic_data(REPOSITORY_ROOT, MANIFEST_PATH, SCHEMA_PATH)

    assert summary.manifest_id == "launchloop_synthetic_v1"
    assert summary.revision == 1
    assert summary.fixture_count == 7
    assert summary.event_count == 6
    assert summary.case_count == 6


def test_changed_fixture_bytes_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    events_path = repository_root / "loops" / "launchloop" / "data" / "events.json"
    events_path.write_bytes(events_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="Checksum mismatch.*events.json"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_missing_and_unlisted_fixture_files_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    policy_path = repository_root / "loops" / "launchloop" / "policies" / "language_policy.md"
    policy_path.unlink()

    with pytest.raises(ValueError, match="Missing fixture file.*language_policy.md"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)

    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path / "orphan")
    orphan_path = repository_root / "loops" / "launchloop" / "data" / "orphan.json"
    orphan_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unlisted fixture file.*orphan.json"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_duplicate_manifest_ids_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    manifest_text = manifest_path.read_text(encoding="utf-8")
    marker = '"launchloop_events": {'
    duplicate_entry = manifest_text[manifest_text.index(marker) :]
    duplicate_entry = duplicate_entry[: duplicate_entry.index("    },") + 6]
    manifest_path.write_text(
        manifest_text.replace(marker, duplicate_entry + "\n    " + marker, 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate JSON key: launchloop_events"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_manifest_and_metadata_fixture_ids_must_match(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    metadata_path = repository_root / "loops" / "launchloop" / "data" / "fixture_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["fixtures"].pop("launchloop_evaluation_cases")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Manifest and metadata fixture IDs differ"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_prohibited_credentials_or_real_contact_data_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    events_path = repository_root / "loops" / "launchloop" / "data" / "events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    events[0]["contact_email"] = "real.person@gmail.com"
    events_path.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Prohibited non-synthetic email domain"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_nested_credential_fields_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    events_path = repository_root / "loops" / "launchloop" / "data" / "events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    events[0]["provider_api_key"] = "not-a-real-key"
    events_path.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Prohibited credential field 'provider_api_key'"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)
