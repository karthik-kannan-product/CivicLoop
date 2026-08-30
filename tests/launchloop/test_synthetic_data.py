import json
import shutil
from pathlib import Path

import pytest

from loops.launchloop.launchloop import run_evals
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
    assert summary.revision == 2
    assert summary.fixture_count == 13
    assert summary.event_count == 15
    assert summary.scenario_count == 15
    assert summary.member_count == 30
    assert summary.sponsor_count == 5
    assert summary.case_count == 6
    assert run_evals()["summary"] == {"passed": 6, "total": 6}


def test_all_required_event_scenarios_are_present(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    events_path = repository_root / "loops" / "launchloop" / "data" / "events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    events[0]["scenario_tags"] = []
    events_path.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing required event scenario: missing_venue"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


@pytest.mark.parametrize(
    ("fixture_name", "reference_field", "expected_error"),
    [
        ("members.json", "segment_id", "Member references unknown segment ID"),
        ("event_histories.json", "event_id", "History references unknown event ID"),
        ("provider_outcomes.json", "event_id", "Provider outcome references unknown event ID"),
    ],
)
def test_associated_fixture_references_must_resolve(
    tmp_path: Path,
    fixture_name: str,
    reference_field: str,
    expected_error: str,
) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    fixture_path = repository_root / "loops" / "launchloop" / "data" / fixture_name
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    records[0][reference_field] = "unknown_reference"
    fixture_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_changed_fixture_bytes_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    events_path = repository_root / "loops" / "launchloop" / "data" / "events.json"
    events_path.write_bytes(events_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="Checksum mismatch.*events.json"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_text_fixture_checksums_ignore_line_ending_style(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    policy_path = repository_root / "loops" / "launchloop" / "policies" / "approval_policy.md"
    original = policy_path.read_bytes()
    alternate = (
        original.replace(b"\r\n", b"\n")
        if b"\r\n" in original
        else original.replace(b"\n", b"\r\n")
    )
    assert alternate != original
    policy_path.write_bytes(alternate)

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


def test_duplicate_member_ids_are_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    members_path = repository_root / "loops" / "launchloop" / "data" / "members.json"
    members = json.loads(members_path.read_text(encoding="utf-8"))
    members[1]["member_id"] = members[0]["member_id"]
    members_path.write_text(json.dumps(members, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate member_id"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_unknown_member_sponsor_is_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    members_path = repository_root / "loops" / "launchloop" / "data" / "members.json"
    members = json.loads(members_path.read_text(encoding="utf-8"))
    members[0]["sponsor_id"] = "unknown_sponsor"
    members_path.write_text(json.dumps(members, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Member references unknown sponsor ID"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_invalid_review_decision_is_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    decisions_path = repository_root / "loops" / "launchloop" / "data" / "review_decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions[0]["decision"] = "auto_approved"
    decisions_path.write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid review decision: auto_approved"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_invalid_provider_result_is_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    outcomes_path = repository_root / "loops" / "launchloop" / "data" / "provider_outcomes.json"
    outcomes = json.loads(outcomes_path.read_text(encoding="utf-8"))
    outcomes[0]["result"] = "silently_retried"
    outcomes_path.write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid provider outcome result: silently_retried"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)


def test_real_member_contact_domain_is_rejected(tmp_path: Path) -> None:
    repository_root, manifest_path, schema_path = _copy_fixture_repository(tmp_path)
    members_path = repository_root / "loops" / "launchloop" / "data" / "members.json"
    members = json.loads(members_path.read_text(encoding="utf-8"))
    members[0]["email"] = "person@gmail.com"
    members_path.write_text(json.dumps(members, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Prohibited non-synthetic email domain"):
        validate_synthetic_data(repository_root, manifest_path, schema_path)
