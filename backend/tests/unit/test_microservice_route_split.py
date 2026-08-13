"""Product API no longer serves routes that moved to practice/content/study."""

from app.main import app


def _paths() -> set[str]:
    return set(app.openapi()["paths"])


def test_practice_routes_left_the_product_api() -> None:
    paths = _paths()
    assert not any(p.startswith("/api/coding") for p in paths)
    assert not any(p.startswith("/api/system-design") for p in paths)
    assert not any(p.startswith("/api/practice") for p in paths)
    assert not any(p.startswith("/api/newspaper") for p in paths)


def test_content_routes_left_the_product_api() -> None:
    paths = _paths()
    assert not any(p.startswith("/api/learn") for p in paths)


def test_study_routes_left_the_product_api() -> None:
    paths = _paths()
    assert not any(p.startswith("/api/artifacts") for p in paths)
    assert not any(p.startswith("/api/mcq") for p in paths)
    assert not any(p.startswith("/api/chat") for p in paths)
    assert not any(p.startswith("/api/progress") for p in paths)
    assert not any(p.startswith("/api/assertions") for p in paths)


def test_product_api_keeps_sources_and_guest() -> None:
    paths = _paths()
    assert any(p.startswith("/api/sources") for p in paths)
    assert any(p == "/api/guest" or p.startswith("/api/guest") for p in paths)
    assert any(p.startswith("/api/documents") for p in paths)
