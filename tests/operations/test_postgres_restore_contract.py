import importlib.util
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_postgres_restore.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_postgres_restore", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _complete_invariants() -> dict[str, list[list[object]]]:
    return {
        "columns": [["public", "events", "id", 1, "bigint", "int8", "NO", None, "NO", None]],
        "constraints": [["public", "events", "events_pkey", "PRIMARY KEY (id)"]],
        "indexes": [["public", "events", "events_pkey", "CREATE UNIQUE INDEX..."]],
        "sequences": [["public", "events_id_seq", "bigint", 1, 1, 12]],
        "views": [["public", "active_events", "SELECT ..."]],
        "materialized_views": [["public", "event_summary", "SELECT ..."]],
        "functions": [["public", "event_count", "", "bigint", "SELECT ..."]],
        "extensions": [["plpgsql", "1.0", "pg_catalog"]],
        "triggers": [["public", "events", "events_audit", "CREATE TRIGGER ..."]],
        "privileges": [["table", "public", "events", "civicloop", "SELECT", "NO"]],
        "counts": [["public", "events", 12]],
    }


def test_restore_validator_compares_all_application_equivalence_invariants() -> None:
    validator = _load_validator()
    expected = _complete_invariants()

    assert validator.validate_invariants(expected, expected) == []
    assert tuple(expected) == validator.INVARIANT_CATEGORIES
    for category in validator.INVARIANT_CATEGORIES:
        restored = {name: list(values) for name, values in expected.items()}
        restored[category] = [["different"]]
        assert validator.validate_invariants(expected, restored) == [
            f"{category} invariant mismatch"
        ]


def test_restore_validator_uses_read_only_repeatable_read_source_transaction() -> None:
    validator = _load_validator()

    class Connection:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def transaction(self):
            return nullcontext()

        def execute(self, command: str) -> None:
            self.commands.append(command)

    connection = Connection()
    with validator.read_only_snapshot(connection):
        pass

    assert connection.commands == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ]


def test_restore_validator_diagnostics_contain_only_digests_and_category_counts() -> None:
    validator = _load_validator()
    expected = _complete_invariants()
    restored = _complete_invariants()
    restored["functions"] = [["secret-function-body-marker"]]

    diagnostic = validator.diagnostic_summary(expected, restored)

    assert "sha256:" in diagnostic
    assert "functions=1" in diagnostic
    assert "secret-function-body-marker" not in diagnostic


def test_restore_validator_requires_separate_database_hosts_without_exposing_passwords() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--source-host",
            "db",
            "--restored-host",
            "db",
        ],
        env={**os.environ, "POSTGRES_PASSWORD": "must-not-appear"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "separate" in result.stderr.lower()
    assert "must-not-appear" not in result.stdout + result.stderr
