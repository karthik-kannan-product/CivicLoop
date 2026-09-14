#!/usr/bin/env python3
"""Compare schema and row-count invariants between live and restored PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql

InvariantSet = dict[str, list[list[Any]]]


def capture_invariants(connection: psycopg.Connection[Any]) -> InvariantSet:
    """Capture metadata and counts without reading or printing record contents."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema, table_name, column_name, data_type,
                   udt_name, is_nullable, ordinal_position
              FROM information_schema.columns
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
             ORDER BY table_schema, table_name, ordinal_position
            """
        )
        columns = [list(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT table_schema, table_name
              FROM information_schema.tables
             WHERE table_type = 'BASE TABLE'
               AND table_schema NOT IN ('pg_catalog', 'information_schema')
             ORDER BY table_schema, table_name
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
    return {"columns": columns, "counts": counts}


def validate_invariants(expected: InvariantSet, restored: InvariantSet) -> list[str]:
    errors = []
    if expected.get("columns") != restored.get("columns"):
        errors.append("schema invariant mismatch")
    if expected.get("counts") != restored.get("counts"):
        errors.append("record-count invariant mismatch")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-host", required=True)
    parser.add_argument("--restored-host", required=True)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default=os.environ.get("POSTGRES_DB"))
    parser.add_argument("--username", default=os.environ.get("POSTGRES_USER"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.source_host == args.restored_host:
        print("Source and restored databases must use separate hosts.", file=sys.stderr)
        return 2
    password = os.environ.get("POSTGRES_PASSWORD")
    if not args.database or not args.username or not password:
        print("Required PostgreSQL connection environment is incomplete.", file=sys.stderr)
        return 2

    connection_args = {
        "port": args.port,
        "dbname": args.database,
        "user": args.username,
        "password": password,
        "connect_timeout": 10,
    }
    try:
        with psycopg.connect(host=args.source_host, **connection_args) as source:
            with psycopg.connect(host=args.restored_host, **connection_args) as restored:
                errors = validate_invariants(
                    capture_invariants(source), capture_invariants(restored)
                )
    except (psycopg.Error, RuntimeError) as exc:
        print(f"PostgreSQL invariant validation failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 1
    print("PostgreSQL restore schema and record-count invariants passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
