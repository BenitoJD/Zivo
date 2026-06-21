"""Execute schema SQL files from Alembic migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import op

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


def execute_sql_file(filename: str) -> None:
    path = SCHEMA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(path)
    sql = path.read_text()
    # psycopg3 treats % in driver SQL as bind placeholders; schema DDL uses literal %.
    sql = sql.replace("%", "%%")
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql(sql)
