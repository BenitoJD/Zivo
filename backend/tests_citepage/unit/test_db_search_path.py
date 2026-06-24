import inspect

from app.db_search_path import SEARCH_PATH_STMT


def test_search_path_stmt_is_transaction_local() -> None:
    assert "SET LOCAL search_path" in str(SEARCH_PATH_STMT)
    assert "qb" in str(SEARCH_PATH_STMT)


def test_db_module_uses_attach_not_startup_options() -> None:
    import app.db as db_mod

    source = inspect.getsource(db_mod)
    assert "attach_search_path" in source
    assert "connect_args" not in source
