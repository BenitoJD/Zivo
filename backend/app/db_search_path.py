"""PgBouncer-safe search_path setup.

Transaction-pooled PgBouncer rejects startup options like ``search_path`` in the
connection string. Set the path at transaction begin instead.
"""

from sqlalchemy import event, text

SEARCH_PATH_STMT = text("SET LOCAL search_path TO qb, intel, public")


def attach_search_path(engine) -> None:
    @event.listens_for(engine, "begin")
    def _set_search_path(conn) -> None:
        conn.execute(SEARCH_PATH_STMT)
