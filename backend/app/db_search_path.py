"""PgBouncer-safe database engine helpers.

Transaction-pooled PgBouncer:
- Rejects startup ``search_path`` options — set per transaction instead.
- Breaks prepared / cached statements — disable on psycopg and asyncpg.
"""

from sqlalchemy import event, text

SEARCH_PATH_STMT = text("SET LOCAL search_path TO qb, intel, public")

# psycopg3: disable server-side prepared statements (txn pooling reassigns backends).
PGBOUNCER_PSYCOPG_CONNECT_ARGS = {"prepare_threshold": None}

# asyncpg: disable statement cache (same reason).
PGBOUNCER_ASYNCPG_CONNECT_ARGS = {"statement_cache_size": 0}


def attach_search_path(engine) -> None:
    @event.listens_for(engine, "begin")
    def _set_search_path(conn) -> None:
        conn.execute(SEARCH_PATH_STMT)
