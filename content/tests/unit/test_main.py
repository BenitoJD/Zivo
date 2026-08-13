"""Process smoke: this service is a FastAPI with the expected title and routes."""

from content_main import app


def _paths() -> set[str]:
    return set(app.openapi()["paths"])


def test_title() -> None:
    assert app.title == "Zivo Content"


def test_health_route() -> None:
    paths = _paths()
    assert "/api/health" in paths


def test_content_routes_mounted() -> None:
    paths = _paths()
    assert any(p.startswith("/api/learn") for p in paths)
    assert any("/newspaper/admin" in p for p in paths)
