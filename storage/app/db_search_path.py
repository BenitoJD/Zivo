"""PgBouncer-safe search_path for the storage schema."""

from __future__ import annotations

from sqlalchemy import event, text

SEARCH_PATH_STMT = text("SET LOCAL search_path TO storage, public")

PGBOUNCER_PSYCOPG_CONNECT_ARGS = {"prepare_threshold": None}


def attach_search_path(engine) -> None:
    @event.listens_for(engine, "begin")
    def _set_search_path(conn) -> None:
        conn.execute(SEARCH_PATH_STMT)
