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

# asyncpg ≥0.28 keeps TWO caches. statement_cache_size=0 alone still leaves
# prepared_statement_cache_size active → DuplicatePreparedStatementError through
# PgBouncer transaction pooling. Unique statement names are belt-and-suspenders
# for any leftover prepare calls (LISTEN connections, dialect edge paths).
PGBOUNCER_ASYNCPG_CONNECT_ARGS = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4().hex}__",
}


def attach_search_path(engine) -> None:
    @event.listens_for(engine, "begin")
    def _set_search_path(conn) -> None:
        conn.execute(SEARCH_PATH_STMT)
