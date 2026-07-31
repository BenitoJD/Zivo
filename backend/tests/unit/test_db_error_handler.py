"""Classification of DB errors for the global ``database_error_handler``.

The handler used to map *every* ``SQLAlchemyError`` to ``503 "Database
unavailable"``. That made two unrelated prod bugs (a query param typing bug and
a violated immutability trigger) both look like a database outage, which is
exactly the misdiagnosis these tests prevent: only genuine connection/infra
failures stay 503; SQL and data errors surface as an honest 500.

These are pure unit tests — they construct ``SQLAlchemyError`` instances with a
fake ``orig`` carrying a ``sqlstate`` (how psycopg3 annotates the real error),
so no database is required and they run in every CI run.
"""

from __future__ import annotations

import types

from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    IntegrityError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)

from app.main import _is_db_outage


def _dbapi_err(cls: type[DBAPIError], sqlstate: str | None) -> DBAPIError:
    orig = types.SimpleNamespace(sqlstate=sqlstate)
    return cls("SELECT 1", {}, orig)


def test_connection_error_is_outage():
    # OperationalError == psycopg2/3 connection-layer (08*). Always 503.
    assert _is_db_outage(_dbapi_err(OperationalError, "08006")) is True


def test_interface_error_is_outage():
    assert _is_db_outage(_dbapi_err(InterfaceError, "08001")) is True


def test_admin_shutdown_is_outage_via_sqlstate():
    # 57P01 = admin_shutdown. SQLAlchemy may raise this as OperationalError; the
    # SQLSTATE fallback must still classify it as an outage.
    assert _is_db_outage(_dbapi_err(OperationalError, "57P01")) is True


def test_insufficient_resources_is_outage():
    # 53100 = insufficient_resources (e.g. disk/memory on the DB host).
    assert _is_db_outage(_dbapi_err(OperationalError, "53100")) is True


def test_ambiguous_parameter_is_not_an_outage():
    # The exact prod bug #1: ProgrammingError with sqlstate 42P10
    # (ambiguous_parameter). Must NOT report as "Database unavailable".
    assert _is_db_outage(_dbapi_err(ProgrammingError, "42P10")) is False


def test_plpgsql_raise_exception_is_not_an_outage():
    # The exact prod bug #2: the immutable-measurement trigger raises
    # P0001 (raise_exception). A server-side RAISE is a logic error, not an
    # outage — the DB is perfectly reachable.
    assert _is_db_outage(_dbapi_err(InternalError, "P0001")) is False


def test_unique_violation_is_not_an_outage():
    assert _is_db_outage(_dbapi_err(IntegrityError, "23505")) is False


def test_syntax_error_is_not_an_outage():
    # 42601 = syntax_error — a bug in the SQL string.
    assert _is_db_outage(_dbapi_err(ProgrammingError, "42601")) is False


def test_feature_not_supported_is_not_an_outage():
    assert _is_db_outage(_dbapi_err(NotSupportedError, "0A000")) is False


def test_dbapi_error_without_sqlstate_defaults_to_not_outage():
    # A DBAPIError with no driver sqlstate carries no signal it's an outage;
    # default to 500 (honest "request failed") rather than masquerading as 503.
    assert _is_db_outage(_dbapi_err(DBAPIError, None)) is False
