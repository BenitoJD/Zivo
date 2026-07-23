"""PgBouncer-safe database engine helpers.

Transaction-pooled PgBouncer:
- Rejects startup ``search_path`` options — set per transaction instead.
- Breaks prepared / cached statements — disable on psycopg and asyncpg.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import event, text

SEARCH_PATH_STMT = text("SET LOCAL search_path TO qb, intel, public")

# psycopg3: disable server-side prepared statements (txn pooling reassigns backends).
PGBOUNCER_PSYCOPG_CONNECT_ARGS = {"prepare_threshold": None}


def _asyncpg_statement_name() -> str:
    return f"__asyncpg_{uuid4().hex}__"


# Raw asyncpg.connect(...) kwargs only. Do NOT put SQLAlchemy-only keys here —
# prepared_statement_cache_size is a SQLAlchemy dialect arg and crashes asyncpg.
PGBOUNCER_ASYNCPG_RAW_CONNECT_ARGS = {
    "statement_cache_size": 0,
    "prepared_statement_name_func": _asyncpg_statement_name,
}

# SQLAlchemy create_async_engine(..., connect_args=...). Includes the dialect-level
# prepared_statement_cache_size (asyncpg ≥0.28 / SQLAlchemy dual-cache) plus the
# raw asyncpg kwargs above.
PGBOUNCER_ASYNCPG_CONNECT_ARGS = {
    **PGBOUNCER_ASYNCPG_RAW_CONNECT_ARGS,
    "prepared_statement_cache_size": 0,
}


def attach_search_path(engine) -> None:
    @event.listens_for(engine, "begin")
    def _set_search_path(conn) -> None:
        conn.execute(SEARCH_PATH_STMT)
