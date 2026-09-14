#!/usr/bin/env python3
"""Compare content-free PostgreSQL application-equivalence invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql

InvariantSet = dict[str, list[list[Any]]]
INVARIANT_CATEGORIES = (
    "columns",
    "constraints",
    "indexes",
    "sequences",
    "views",
    "materialized_views",
    "functions",
    "extensions",
    "types",
    "row_security_policies",
    "triggers",
    "privileges",
    "counts",
)

CATALOG_QUERIES = {
    "columns": """
        SELECT n.nspname, c.relname, a.attname, a.attnum,
               pg_catalog.format_type(a.atttypid, a.atttypmod), t.typname,
               CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END,
               pg_catalog.pg_get_expr(ad.adbin, ad.adrelid),
               a.attidentity, a.attgenerated, coll.collname
          FROM pg_catalog.pg_attribute a
          JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
          LEFT JOIN pg_catalog.pg_attrdef ad
                 ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
          LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = a.attcollation
         WHERE a.attnum > 0 AND NOT a.attisdropped
           AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname !~ '^pg_toast'
         ORDER BY n.nspname, c.relname, a.attnum
    """,
    "constraints": """
        SELECT n.nspname, c.relname, con.conname, con.contype,
               pg_catalog.pg_get_constraintdef(con.oid, true),
               con.condeferrable, con.condeferred, con.convalidated
          FROM pg_catalog.pg_constraint con
          JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname !~ '^pg_toast'
         ORDER BY n.nspname, c.relname, con.conname
    """,
    "indexes": """
        SELECT n.nspname, tc.relname, ic.relname,
               pg_catalog.pg_get_indexdef(ic.oid),
               i.indisunique, i.indisprimary, i.indisvalid, i.indisready
          FROM pg_catalog.pg_index i
          JOIN pg_catalog.pg_class tc ON tc.oid = i.indrelid
          JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = tc.relnamespace
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname !~ '^pg_toast'
         ORDER BY n.nspname, tc.relname, ic.relname
    """,
    "sequences": """
        SELECT schemaname, sequencename, sequenceowner, data_type,
               start_value, min_value, max_value, increment_by,
               cycle, cache_size, last_value
          FROM pg_catalog.pg_sequences
         WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
         ORDER BY schemaname, sequencename
    """,
    "views": """
        SELECT schemaname, viewname, viewowner, definition
          FROM pg_catalog.pg_views
         WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
         ORDER BY schemaname, viewname
    """,
    "materialized_views": """
        SELECT schemaname, matviewname, matviewowner, ispopulated, definition
          FROM pg_catalog.pg_matviews
         WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
         ORDER BY schemaname, matviewname
    """,
    "functions": """
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid),
               pg_catalog.pg_get_function_result(p.oid), p.prokind,
               p.provolatile, p.proparallel, p.prosecdef, l.lanname,
               pg_catalog.pg_get_functiondef(p.oid)
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_catalog.pg_language l ON l.oid = p.prolang
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname !~ '^pg_toast'
           AND p.prokind IN ('f', 'p', 'w')
         ORDER BY n.nspname, p.proname,
                  pg_catalog.pg_get_function_identity_arguments(p.oid)
    """,
    "extensions": """
        SELECT e.extname, e.extversion, n.nspname, e.extrelocatable
          FROM pg_catalog.pg_extension e
          JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
         ORDER BY e.extname
    """,
    "types": """
        SELECT 'enum', n.nspname, t.typname, e.enumlabel,
               e.enumsortorder::text, NULL, NULL
          FROM pg_catalog.pg_type t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
          JOIN pg_catalog.pg_enum e ON e.enumtypid = t.oid
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'domain', n.nspname, t.typname,
               pg_catalog.format_type(t.typbasetype, t.typtypmod),
               t.typnotnull::text, pg_catalog.pg_get_expr(t.typdefaultbin, 0),
               pg_catalog.pg_get_constraintdef(con.oid, true)
          FROM pg_catalog.pg_type t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
          LEFT JOIN pg_catalog.pg_constraint con ON con.contypid = t.oid
         WHERE t.typtype = 'd'
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'composite', n.nspname, t.typname, a.attname,
               a.attnum::text,
               pg_catalog.format_type(a.atttypid, a.atttypmod),
               a.attnotnull::text
          FROM pg_catalog.pg_type t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
          JOIN pg_catalog.pg_class c ON c.oid = t.typrelid
          JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
         WHERE t.typtype = 'c' AND c.relkind = 'c'
           AND a.attnum > 0 AND NOT a.attisdropped
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'range', n.nspname, t.typname,
               pg_catalog.format_type(r.rngsubtype, NULL),
               concat_ws('.', cn.nspname, coll.collname),
               concat_ws('.', onsp.nspname, opc.opcname),
               concat_ws(',', r.rngcanonical::regproc::text, r.rngsubdiff::regproc::text)
          FROM pg_catalog.pg_type t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
          JOIN pg_catalog.pg_range r ON r.rngtypid = t.oid
          JOIN pg_catalog.pg_opclass opc ON opc.oid = r.rngsubopc
          JOIN pg_catalog.pg_namespace onsp ON onsp.oid = opc.opcnamespace
          LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = r.rngcollation
          LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid = coll.collnamespace
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'multirange', n.nspname, t.typname,
               pg_catalog.format_type(r.rngsubtype, NULL),
               concat_ws('.', cn.nspname, coll.collname),
               concat_ws('.', onsp.nspname, opc.opcname),
               concat_ws(',', r.rngcanonical::regproc::text, r.rngsubdiff::regproc::text)
          FROM pg_catalog.pg_type t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
          JOIN pg_catalog.pg_range r ON r.rngmultitypid = t.oid
          JOIN pg_catalog.pg_opclass opc ON opc.oid = r.rngsubopc
          JOIN pg_catalog.pg_namespace onsp ON onsp.oid = opc.opcnamespace
          LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = r.rngcollation
          LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid = coll.collnamespace
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
         ORDER BY 1, 2, 3, 4, 5, 6, 7
    """,
    "row_security_policies": """
        SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
               p.polname, p.polpermissive, p.polcmd,
               ARRAY(
                 SELECT CASE WHEN role_oid = 0 THEN 'public'
                             ELSE pg_catalog.pg_get_userbyid(role_oid) END
                   FROM unnest(p.polroles) AS role_oid
                  ORDER BY 1
               ),
               pg_catalog.pg_get_expr(p.polqual, p.polrelid),
               pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid)
          FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_catalog.pg_policy p ON p.polrelid = c.oid
         WHERE c.relkind IN ('r', 'p')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname !~ '^pg_toast'
         ORDER BY n.nspname, c.relname, p.polname
    """,
    "triggers": """
        SELECT n.nspname, c.relname, t.tgname,
               pg_catalog.pg_get_triggerdef(t.oid, true), t.tgenabled
          FROM pg_catalog.pg_trigger t
          JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE NOT t.tgisinternal
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname !~ '^pg_toast'
         ORDER BY n.nspname, c.relname, t.tgname
    """,
    "privileges": """
        SELECT 'table', table_schema, table_name, grantee, privilege_type,
               is_grantable
          FROM information_schema.table_privileges
         WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'column', table_schema, table_name || '.' || column_name,
               grantee, privilege_type, is_grantable
          FROM information_schema.column_privileges
         WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'routine', routine_schema, routine_name, grantee,
               privilege_type, is_grantable
          FROM information_schema.routine_privileges
         WHERE routine_schema NOT IN ('pg_catalog', 'information_schema')
        UNION ALL
        SELECT 'usage', object_schema, object_name, grantee,
               privilege_type, is_grantable
          FROM information_schema.usage_privileges
         WHERE object_schema NOT IN ('pg_catalog', 'information_schema')
         ORDER BY 1, 2, 3, 4, 5, 6
    """,
}


@contextmanager
def read_only_snapshot(connection: Any) -> Iterator[None]:
    """Run catalog reads in a repeatable, explicitly read-only transaction."""
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield


def capture_invariants(connection: psycopg.Connection[Any]) -> InvariantSet:
    """Capture metadata and counts without returning record contents."""
    invariants: InvariantSet = {}
    with connection.cursor() as cursor:
        for category, query in CATALOG_QUERIES.items():
            cursor.execute(query)
            invariants[category] = [list(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT n.nspname, c.relname
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p')
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname !~ '^pg_toast'
             ORDER BY n.nspname, c.relname
            """
        )
        tables = cursor.fetchall()
        counts: list[list[Any]] = []
        for schema_name, table_name in tables:
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema_name), sql.Identifier(table_name)
                )
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("record-count query returned no result")
            counts.append([schema_name, table_name, row[0]])
        invariants["counts"] = counts
    return invariants


def invariant_digest(invariants: InvariantSet) -> str:
    canonical = json.dumps(
        invariants, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate_invariants(expected: InvariantSet, restored: InvariantSet) -> list[str]:
    return [
        f"{category} invariant mismatch"
        for category in INVARIANT_CATEGORIES
        if expected.get(category) != restored.get(category)
    ]


def diagnostic_summary(expected: InvariantSet, restored: InvariantSet) -> str:
    def counts(invariants: InvariantSet) -> str:
        return ",".join(
            f"{category}={len(invariants.get(category, []))}"
            for category in INVARIANT_CATEGORIES
        )

    return (
        f"source={invariant_digest(expected)} restored={invariant_digest(restored)} "
        f"source_counts[{counts(expected)}] restored_counts[{counts(restored)}]"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-host", required=True)
    parser.add_argument("--restored-host")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--expected-digest")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default=os.environ.get("POSTGRES_DB"))
    parser.add_argument("--username", default=os.environ.get("POSTGRES_USER"))
    return parser


def _capture(host: str, connection_args: dict[str, object]) -> InvariantSet:
    with psycopg.connect(host=host, autocommit=True, **connection_args) as connection:
        with read_only_snapshot(connection):
            return capture_invariants(connection)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.source_only and not args.restored_host:
        print("A separate restored database host is required.", file=sys.stderr)
        return 2
    if args.restored_host == args.source_host:
        print("Source and restored databases must use separate hosts.", file=sys.stderr)
        return 2
    if args.expected_digest and not re.fullmatch(r"sha256:[a-f0-9]{64}", args.expected_digest):
        print("Expected invariant digest is invalid.", file=sys.stderr)
        return 2
    password = os.environ.get("POSTGRES_PASSWORD")
    if not args.database or not args.username or not password:
        print("Required PostgreSQL connection environment is incomplete.", file=sys.stderr)
        return 2

    connection_args: dict[str, object] = {
        "port": args.port,
        "dbname": args.database,
        "user": args.username,
        "password": password,
        "connect_timeout": 10,
    }
    try:
        source = _capture(args.source_host, connection_args)
        source_digest = invariant_digest(source)
        if args.expected_digest and source_digest != args.expected_digest:
            print(f"Source invariant digest mismatch: {source_digest}", file=sys.stderr)
            return 1
        if args.source_only:
            print(source_digest)
            return 0
        restored = _capture(args.restored_host, connection_args)
    except (psycopg.Error, RuntimeError) as exc:
        print(f"PostgreSQL invariant validation failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    errors = validate_invariants(source, restored)
    if errors:
        print("; ".join(errors), file=sys.stderr)
        print(diagnostic_summary(source, restored), file=sys.stderr)
        return 1
    print(f"PostgreSQL restore invariants passed: {source_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
