import copy
import importlib.util
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_postgres_restore.py"


def _load_validator() -> ModuleType:
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
        "types": [["enum", "public", "event_status", "active", 1.0]],
        "row_security_policies": [
            ["public", "events", True, True, "owner_only", True, ["civicloop"], "r"]
        ],
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

        def transaction(self) -> nullcontext[None]:
            return nullcontext()

        def execute(self, command: str) -> None:
            self.commands.append(command)

    connection = Connection()
    with validator.read_only_snapshot(connection):
        pass

    assert connection.commands == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"]


def test_restore_validator_diagnostics_contain_only_digests_and_category_counts() -> None:
    validator = _load_validator()
    expected = _complete_invariants()
    restored = _complete_invariants()
    restored["functions"] = [["secret-function-body-marker"]]

    diagnostic = validator.diagnostic_summary(expected, restored)

    assert "sha256:" in diagnostic
    assert "functions=1" in diagnostic
    assert "secret-function-body-marker" not in diagnostic


def test_restore_validator_detects_user_defined_type_and_rls_policy_mismatches() -> None:
    validator = _load_validator()
    expected = _complete_invariants()
    restored = _complete_invariants()
    restored["types"] = [["enum", "public", "event_status", "disabled", 2.0]]
    restored["row_security_policies"] = []

    assert validator.validate_invariants(expected, restored) == [
        "types invariant mismatch",
        "row_security_policies invariant mismatch",
    ]


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


@pytest.mark.parametrize("parenthesized", [False, True])
def test_literal_array_cast_distribution_is_canonical(parenthesized: bool) -> None:
    validator = _load_validator()
    array = "ARRAY['active'::character varying, 'disabled'::character varying]"
    element = "ARRAY['active'::character varying::text, 'disabled'::character varying::text]"
    if parenthesized:
        array = f"({array})"
        element = (
            "ARRAY[('active'::character varying)::text, ('disabled'::character varying)::text]"
        )
    source = f"CHECK (status::text = ANY ({array}::text[]))"
    restored = f"CHECK (status::text = ANY ({element}))"
    assert validator.normalize_catalog_definition(source) == validator.normalize_catalog_definition(
        restored
    )


def test_partial_unique_index_keeps_predicate_and_uniqueness() -> None:
    validator = _load_validator()
    prefix = "CREATE UNIQUE INDEX active_run ON public.runs USING btree (lane) WHERE "
    source = (
        prefix + "(lane AND ((status)::text = ANY ((ARRAY['queued'::character varying, "
        "'running'::character varying])::text[])))"
    )
    restored = (
        prefix + "(lane AND ((status)::text = ANY (ARRAY[('queued'::character varying)::text, "
        "('running'::character varying)::text])))"
    )
    canonical = validator.normalize_catalog_definition(source)
    assert canonical == validator.normalize_catalog_definition(restored)
    for mutation in (
        restored.replace("UNIQUE ", ""),
        restored.replace(" AND ", " OR "),
        restored.replace("lane AND", "NOT lane AND"),
        restored.replace(" = ANY", " <> ANY"),
    ):
        assert canonical != validator.normalize_catalog_definition(mutation)


@pytest.mark.parametrize(
    "mutation",
    [
        "ARRAY['changed'::character varying, 'disabled'::character varying]::text[]",
        "ARRAY['disabled'::character varying, 'active'::character varying]::text[]",
        "ARRAY['active'::character varying]::text[]",
        "ARRAY['active'::character varying, 'disabled'::character varying]::integer[]",
    ],
)
def test_array_literals_order_cardinality_and_target_type_remain_invariants(mutation: str) -> None:
    validator = _load_validator()
    source = "ARRAY['active'::character varying, 'disabled'::character varying]::text[]"
    assert validator.normalize_catalog_definition(source) != validator.normalize_catalog_definition(
        mutation
    )


@pytest.mark.parametrize(
    "definition",
    [
        "ARRAY['x'::character varying(8)]::text[]",
        "ARRAY['x'::char]::text[]",
        "ARRAY['x'::custom_domain]::text[]",
        "ARRAY['x'::character varying COLLATE \"C\"]::text[]",
        "ARRAY[lower('x')::character varying]::text[]",
        "ARRAY[NULL::character varying]::text[]",
        "ARRAY[ARRAY['x'::character varying]::text[]]",
        "ARRAY[]::text[]",
        "foo(ARRAY['x'::character varying])::text[]",
        "\"foo\"(ARRAY['x'::character varying])::text[]",
        "ARRAY['x'::character varying::text, 'y'::character varying]::text[]",
        "ARRAY[E'x'::character varying]::text[]",
        r"ARRAY['x\y'::character varying]::text[]",
        "$$ ARRAY['x'::character varying]::text[] $$",
        "/* ARRAY['x'::character varying]::text[] */",
        "-- ARRAY['x'::character varying]::text[]",
        "\"ARRAY['x'::character varying]::text[]\"",
        "'ARRAY[''x''::character varying]::text[]'",
    ],
)
def test_unsupported_sql_and_opaque_quoted_content_are_unchanged(definition: str) -> None:
    validator = _load_validator()
    assert validator.normalize_catalog_definition(definition) == definition


def test_escaped_standard_literal_remains_opaque_and_preserved() -> None:
    validator = _load_validator()
    source = "ARRAY['it''s ARRAY[NULL] -- not SQL'::character varying]::text[]"
    restored = "ARRAY['it''s ARRAY[NULL] -- not SQL'::character varying::text]"
    canonical = validator.normalize_catalog_definition(source)
    assert canonical == validator.normalize_catalog_definition(restored)
    assert "'it''s ARRAY[NULL] -- not SQL'" in canonical


def test_logical_column_order_defaults_and_constraint_flags_still_fail() -> None:
    validator = _load_validator()
    query = validator.CATALOG_QUERIES["columns"]
    assert "row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum)" in query
    assert "NOT a.attisdropped" in query
    expected = _complete_invariants()
    for category, index, value in (
        ("columns", 3, 2),
        ("columns", 7, "changed default"),
        ("constraints", 3, "CHECK (false)"),
        ("indexes", 3, "CREATE INDEX changed"),
    ):
        restored = copy.deepcopy(expected)
        restored[category][0][index] = value
        assert validator.validate_invariants(expected, restored) == [
            f"{category} invariant mismatch"
        ]


@pytest.mark.parametrize(
    "category,index,value",
    [
        ("constraints", 5, False),
        ("constraints", 6, True),
        ("constraints", 7, False),
        ("indexes", 4, False),
        ("indexes", 5, True),
        ("indexes", 6, False),
        ("indexes", 7, False),
    ],
)
def test_constraint_and_index_security_properties_remain_invariants(
    category: str, index: int, value: bool
) -> None:
    validator = _load_validator()
    expected = _complete_invariants()
    expected["constraints"] = [
        ["public", "events", "check", "c", "CHECK (status = 'active')", True, False, True]
    ]
    expected["indexes"] = [
        [
            "public",
            "events",
            "idx",
            "CREATE UNIQUE INDEX idx ON events(id)",
            True,
            False,
            True,
            True,
        ]
    ]
    restored = copy.deepcopy(expected)
    restored[category][0][index] = value
    assert validator.validate_invariants(expected, restored) == [f"{category} invariant mismatch"]
