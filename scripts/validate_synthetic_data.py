import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "loops" / "launchloop" / "data" / "manifest.json"
SCHEMA_PATH = REPOSITORY_ROOT / "schemas" / "agents" / "fixture-manifest.schema.json"
METADATA_FIXTURE_ID = "launchloop_fixture_metadata"
MEMBERS_FIXTURE_ID = "launchloop_members"
SPONSORS_FIXTURE_ID = "launchloop_sponsors"
HISTORIES_FIXTURE_ID = "launchloop_event_histories"
TEMPLATES_FIXTURE_ID = "launchloop_content_templates"
DECISIONS_FIXTURE_ID = "launchloop_review_decisions"
OUTCOMES_FIXTURE_ID = "launchloop_provider_outcomes"
LABELED_EXAMPLES_FIXTURE_ID = "launchloop_labeled_examples"
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
REQUIRED_SCENARIO_TAGS = frozenset(
    {
        "accessibility_inconsistency",
        "ambiguous_dst_timezone",
        "bilingual_policy",
        "complete_event",
        "confirmed_revision",
        "duplicate_delivery_stale_revision",
        "free_event",
        "invalid_signup_link",
        "missing_segment",
        "missing_venue",
        "online_hybrid_event",
        "prompt_injection",
        "rescheduled_event",
        "sponsor_tier_mismatch",
        "suppressed_audience",
    }
)
MEMBER_STATUSES = {"active", "suppressed", "unsubscribed"}
SPONSOR_RULES = {"platinum": 25, "gold": 25, "silver": 15, "bronze": 15}
REVIEW_DECISIONS = {"approved", "edited", "rejected", "invalidated"}
PROVIDER_RESULTS = {
    "duplicate",
    "permanent_failure",
    "stale_revision",
    "success",
    "timeout",
    "transient_failure",
}
LABELED_EXAMPLE_FIELDS = {
    "case_id",
    "case_type",
    "event_id",
    "example_id",
    "expected_human_handoff",
    "expected_risk_flags",
    "expected_status",
    "input_variant",
    "synthetic",
}


@dataclass(frozen=True)
class SyntheticDataSummary:
    manifest_id: str
    revision: int
    fixture_count: int
    event_count: int
    scenario_count: int
    member_count: int
    sponsor_count: int
    case_count: int
    labeled_example_count: int


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


def _fixture_digest(path: Path) -> str:
    if path.suffix in {".json", ".md"}:
        content = path.read_text(encoding="utf-8").encode("utf-8")
    else:
        content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()


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
    evaluations_root = launchloop_root / "evaluations"
    if evaluations_root.exists():
        discovered.update(
            path.relative_to(repository_root).as_posix()
            for path in evaluations_root.glob("*.json")
        )
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


def _require_fixture_list(loaded_json: dict[str, Any], fixture_id: str) -> list[Any]:
    fixture = loaded_json.get(fixture_id)
    if not isinstance(fixture, list):
        raise ValueError(f"Fixture {fixture_id} must be a JSON array")
    return fixture


def _require_rfc3339(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be an RFC 3339 timestamp")


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
    segments = _require_fixture_list(loaded_json, "launchloop_audience_segments")
    segment_ids = _require_unique_ids(
        segments,
        "segment_id",
        resolved_paths["launchloop_audience_segments"],
    )
    represented_scenarios: set[str] = set()
    empty_scenario_event: str | None = None
    for event in events:
        scenario_tags = event.get("scenario_tags")
        if (
            not isinstance(scenario_tags, list)
            or len(scenario_tags) != len(set(scenario_tags))
            or any(
                not isinstance(tag, str) or tag not in REQUIRED_SCENARIO_TAGS
                for tag in scenario_tags
            )
        ):
            raise ValueError(f"Invalid scenario_tags for event {event['event_id']}")
        if not scenario_tags:
            empty_scenario_event = event["event_id"]
        represented_scenarios.update(scenario_tags)
    missing_scenarios = sorted(REQUIRED_SCENARIO_TAGS - represented_scenarios)
    if missing_scenarios:
        raise ValueError(f"Missing required event scenario: {missing_scenarios[0]}")
    if empty_scenario_event is not None:
        raise ValueError(f"Invalid scenario_tags for event {empty_scenario_event}")
    case_ids = _require_unique_ids(
        cases,
        "case_id",
        resolved_paths["launchloop_evaluation_cases"],
    )
    case_by_id = {case["case_id"]: case for case in cases}
    for case in cases:
        if case["event_id"] not in event_ids:
            raise ValueError(f"Evaluation case references unknown event ID: {case['event_id']}")

    labeled_document = loaded_json.get(LABELED_EXAMPLES_FIXTURE_ID)
    if not isinstance(labeled_document, dict) or set(labeled_document) != {
        "schema_version",
        "examples",
    }:
        raise ValueError("Labeled example fixture must contain schema_version and examples")
    if labeled_document["schema_version"] != "1.0":
        raise ValueError("Unsupported labeled example schema version")
    labeled_examples = labeled_document["examples"]
    if not isinstance(labeled_examples, list) or len(labeled_examples) < 100:
        raise ValueError("Labeled example fixture must contain at least 100 records")
    _require_unique_ids(
        labeled_examples,
        "example_id",
        resolved_paths[LABELED_EXAMPLES_FIXTURE_ID],
    )
    represented_case_ids: set[str] = set()
    for example in labeled_examples:
        if not isinstance(example, dict) or set(example) != LABELED_EXAMPLE_FIELDS:
            raise ValueError("Labeled example has unexpected or missing fields")
        case_id = example.get("case_id")
        if case_id not in case_ids:
            raise ValueError(f"Labeled example references unknown case ID: {case_id}")
        case = case_by_id[case_id]
        if example.get("case_type") != case["type"]:
            raise ValueError(
                f"Labeled example type does not match case: {example['example_id']}"
            )
        if not isinstance(example.get("input_variant"), str) or not re.fullmatch(
            r"synthetic_variant_\d{2}", example["input_variant"]
        ):
            raise ValueError(
                f"Invalid labeled example input variant: {example['example_id']}"
            )
        if example.get("event_id") != case["event_id"]:
            raise ValueError(f"Labeled example event does not match case: {example['example_id']}")
        expected = case["expected"]
        if example.get("expected_status") != expected["status"]:
            raise ValueError(f"Labeled example status does not match case: {example['example_id']}")
        if example.get("expected_risk_flags") != expected["must_have_risk_flags"]:
            raise ValueError(f"Labeled example risks do not match case: {example['example_id']}")
        if (
            example.get("expected_human_handoff")
            is not expected["must_require_human_handoff"]
        ):
            raise ValueError(
                f"Labeled example handoff does not match case: {example['example_id']}"
            )
        if example.get("synthetic") is not True:
            raise ValueError(f"Labeled example must be synthetic: {example['example_id']}")
        represented_case_ids.add(case_id)
    if represented_case_ids != case_ids:
        raise ValueError("Labeled examples must cover every executable case")

    members = _require_fixture_list(loaded_json, MEMBERS_FIXTURE_ID)
    sponsors = _require_fixture_list(loaded_json, SPONSORS_FIXTURE_ID)
    histories = _require_fixture_list(loaded_json, HISTORIES_FIXTURE_ID)
    templates = _require_fixture_list(loaded_json, TEMPLATES_FIXTURE_ID)
    decisions = _require_fixture_list(loaded_json, DECISIONS_FIXTURE_ID)
    outcomes = _require_fixture_list(loaded_json, OUTCOMES_FIXTURE_ID)
    _require_unique_ids(members, "member_id", resolved_paths[MEMBERS_FIXTURE_ID])
    sponsor_ids = _require_unique_ids(sponsors, "sponsor_id", resolved_paths[SPONSORS_FIXTURE_ID])
    _require_unique_ids(histories, "history_id", resolved_paths[HISTORIES_FIXTURE_ID])
    template_ids = _require_unique_ids(
        templates,
        "template_id",
        resolved_paths[TEMPLATES_FIXTURE_ID],
    )
    _require_unique_ids(decisions, "decision_id", resolved_paths[DECISIONS_FIXTURE_ID])
    _require_unique_ids(outcomes, "outcome_id", resolved_paths[OUTCOMES_FIXTURE_ID])

    if len(members) < 30:
        raise ValueError("Synthetic member fixture must contain at least 30 records")
    if len(sponsors) < 5:
        raise ValueError("Synthetic sponsor fixture must contain at least 5 records")
    member_statuses: set[str] = set()
    for member in members:
        status = member.get("status")
        if status not in MEMBER_STATUSES:
            raise ValueError(f"Invalid member status for {member['member_id']}")
        member_statuses.add(status)
        segment_id = member.get("segment_id")
        if segment_id is None:
            if member.get("clarification_required") is not True:
                raise ValueError(
                    f"Member without segment requires clarification: {member['member_id']}"
                )
        elif segment_id not in segment_ids:
            raise ValueError(f"Member references unknown segment ID: {segment_id}")
        sponsor_id = member.get("sponsor_id")
        if sponsor_id is not None and sponsor_id not in sponsor_ids:
            raise ValueError(f"Member references unknown sponsor ID: {sponsor_id}")
    if not {"suppressed", "unsubscribed"}.issubset(member_statuses):
        raise ValueError("Member fixture must cover suppressed and unsubscribed states")

    for sponsor in sponsors:
        tier = sponsor.get("tier")
        if tier not in SPONSOR_RULES or sponsor.get("discount_percent") != SPONSOR_RULES[tier]:
            raise ValueError(f"Invalid sponsor rule for {sponsor['sponsor_id']}")

    for history in histories:
        if history.get("event_id") not in event_ids:
            raise ValueError(f"History references unknown event ID: {history.get('event_id')}")
        if not isinstance(history.get("revision"), int) or history["revision"] < 1:
            raise ValueError(f"Invalid history revision for {history['history_id']}")
        _require_rfc3339(history.get("occurred_at"), "occurred_at")

    for template in templates:
        if template.get("channel") not in {"invitation", "reminder", "social"}:
            raise ValueError(f"Invalid template channel for {template['template_id']}")
        if template.get("locale") not in {"en", "fr"}:
            raise ValueError(f"Invalid template locale for {template['template_id']}")
        if not isinstance(template.get("version"), int) or template["version"] < 1:
            raise ValueError(f"Invalid template version for {template['template_id']}")

    represented_decisions: set[str] = set()
    for decision in decisions:
        if decision.get("event_id") not in event_ids:
            raise ValueError(
                f"Review decision references unknown event ID: {decision.get('event_id')}"
            )
        if decision.get("template_id") not in template_ids:
            raise ValueError(
                "Review decision references unknown template ID: "
                f"{decision.get('template_id')}"
            )
        decision_value = decision.get("decision")
        if decision_value not in REVIEW_DECISIONS:
            raise ValueError(f"Invalid review decision: {decision_value}")
        represented_decisions.add(decision_value)
        _require_rfc3339(decision.get("decided_at"), "decided_at")
    if represented_decisions != REVIEW_DECISIONS:
        raise ValueError("Review fixture must cover all required decision states")

    represented_results: set[str] = set()
    for outcome in outcomes:
        if outcome.get("event_id") not in event_ids:
            raise ValueError(
                f"Provider outcome references unknown event ID: {outcome.get('event_id')}"
            )
        result = outcome.get("result")
        if result not in PROVIDER_RESULTS:
            raise ValueError(f"Invalid provider outcome result: {result}")
        represented_results.add(result)
        if not isinstance(outcome.get("attempt"), int) or outcome["attempt"] < 1:
            raise ValueError(f"Invalid provider attempt for {outcome['outcome_id']}")
        _require_rfc3339(outcome.get("occurred_at"), "occurred_at")
    if represented_results != PROVIDER_RESULTS:
        raise ValueError("Provider fixture must cover all required outcome states")

    for fixture_id, entry in fixtures.items():
        actual_hash = _fixture_digest(resolved_paths[fixture_id])
        if actual_hash != entry["sha256"]:
            raise ValueError(f"Checksum mismatch for {entry['path']}")

    return SyntheticDataSummary(
        manifest_id=manifest["manifest_id"],
        revision=manifest["revision"],
        fixture_count=len(fixtures),
        event_count=len(events),
        scenario_count=len(represented_scenarios),
        member_count=len(members),
        sponsor_count=len(sponsors),
        case_count=len(cases),
        labeled_example_count=len(labeled_examples),
    )


def main() -> int:
    summary = validate_synthetic_data()
    print(
        f"Validated {summary.fixture_count} synthetic fixtures, "
        f"{summary.event_count} events, {summary.member_count} members, "
        f"{summary.sponsor_count} sponsors, {summary.case_count} evaluation cases, "
        f"and {summary.labeled_example_count} labeled examples "
        f"for {summary.manifest_id} revision {summary.revision}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
