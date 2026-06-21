#!/usr/bin/env python3
"""Apply intel_foundation.sql when the intel schema is not present."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg


def db_url() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def schema_applied(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'intel'"
        )
        return cur.fetchone() is not None


def main() -> int:
    sql_path = Path(__file__).resolve().parents[1] / "schema" / "intel_foundation.sql"
    if not sql_path.exists():
        print(f"schema file missing: {sql_path}", file=sys.stderr)
        return 1

    sql = sql_path.read_text()
    with psycopg.connect(db_url()) as conn:
        if schema_applied(conn):
            print("intel schema already present; skipping apply")
            return 0

    with psycopg.connect(db_url()) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)

    print("intel foundation schema applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
