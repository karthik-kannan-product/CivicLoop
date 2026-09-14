import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_postgres_restore.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_postgres_restore", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restore_validator_compares_schema_and_record_count_invariants() -> None:
    validator = _load_validator()
    expected = {
        "columns": [["public", "events", "id", "bigint", "NO"]],
        "counts": [["public", "events", 12]],
    }

    assert validator.validate_invariants(expected, expected) == []
    assert validator.validate_invariants(
        expected,
        {
            "columns": [["public", "events", "id", "integer", "NO"]],
            "counts": [["public", "events", 11]],
        },
    ) == ["schema invariant mismatch", "record-count invariant mismatch"]


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
