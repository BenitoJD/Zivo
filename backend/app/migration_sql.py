"""Execute schema SQL files from Alembic migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import op

from app.engine_runtime import pick

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


def _raise(exc: BaseException) -> None:
    raise exc


def execute_sql_file(filename: str) -> None:
    path = SCHEMA_DIR / filename
    pick(not path.exists(), lambda: _raise(FileNotFoundError(path)), lambda: None)
    sql = path.read_text()
    # psycopg3 treats % in driver SQL as bind placeholders; schema DDL uses literal %.
    sql = sql.replace("%", "%%")
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql(sql)
