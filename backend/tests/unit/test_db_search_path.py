import inspect

from app.db_search_path import (
    PGBOUNCER_ASYNCPG_CONNECT_ARGS,
    PGBOUNCER_PSYCOPG_CONNECT_ARGS,
    SEARCH_PATH_STMT,
)


def test_search_path_stmt_is_transaction_local() -> None:
    assert "SET LOCAL search_path" in str(SEARCH_PATH_STMT)
    assert "qb" in str(SEARCH_PATH_STMT)


def test_db_module_uses_attach_not_startup_options() -> None:
    import app.db as db_mod

    source = inspect.getsource(db_mod)
    assert "attach_search_path" in source
    assert "PGBOUNCER_PSYCOPG_CONNECT_ARGS" in source
    assert '"options"' not in source


def test_pgbouncer_connect_args_disable_prepared_statements() -> None:
    assert PGBOUNCER_PSYCOPG_CONNECT_ARGS["prepare_threshold"] is None
    assert PGBOUNCER_ASYNCPG_CONNECT_ARGS["statement_cache_size"] == 0
    assert PGBOUNCER_ASYNCPG_CONNECT_ARGS["prepared_statement_cache_size"] == 0
    name_a = PGBOUNCER_ASYNCPG_CONNECT_ARGS["prepared_statement_name_func"]()
    name_b = PGBOUNCER_ASYNCPG_CONNECT_ARGS["prepared_statement_name_func"]()
    assert name_a != name_b
    assert name_a.startswith("__asyncpg_")
