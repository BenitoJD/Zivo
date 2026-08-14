import os
from collections.abc import Generator

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.db_search_path import PGBOUNCER_PSYCOPG_CONNECT_ARGS, attach_search_path

settings = get_settings()

# Keep pool small per process — PgBouncer (or similar) should multiplex
# client connections to Postgres in production; each API/worker pod only
# needs a handful of real server connections.
_pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    connect_args=PGBOUNCER_PSYCOPG_CONNECT_ARGS,
)
attach_search_path(engine)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

QB_METADATA = MetaData(schema="qb")


class Base(DeclarativeBase):
    metadata = QB_METADATA


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


# SQLSTATE class prefixes for transient outage / infra conditions. Anything here
# is genuinely "the DB is unreachable or refusing the operation right now" and
# deserves a 503 with a retry hint. See https://www.postgresql.org/docs/current/errcodes-appendix.html
_OUTAGE_SQLSTATE_CLASSES = {"08", "53", "57"}  # connection / insufficient_resources / operator_intervention


def is_db_outage(exc: SQLAlchemyError) -> bool:
    """True only for connection / infra failures, not for SQL or data bugs.

    A blanket ``except SQLAlchemyError -> 503`` (the old handler) turned every
    DB error into "Database unavailable" — so a typo in a query (ProgrammingError)
    or a violated constraint (IntegrityError) reported itself as an outage. That
    is how two unrelated bugs (AmbiguousParameter on document open, and the
    immutable-measurement trigger on signup) hid behind one misleading message.

    We classify by SQLAlchemy subclass first (OperationalError/InterfaceError are
    always connection-layer), then fall back to the driver's SQLSTATE class for
    cases SQLAlchemy doesn't subsume (e.g. a server-side admin shutdown, 57P01,
    still arrives as OperationalError but some clustered/proxy setups raise a
    bare DBAPIError with the state set).
    """
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return bool(sqlstate) and sqlstate[:2] in _OUTAGE_SQLSTATE_CLASSES
