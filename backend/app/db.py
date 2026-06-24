import os
from collections.abc import Generator

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.db_search_path import attach_search_path

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
