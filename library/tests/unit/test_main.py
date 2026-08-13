"""Process smoke: this service is a FastAPI with the expected title and routes."""

from library_main import app


def _paths() -> set[str]:
    return set(app.openapi()["paths"])


def test_title() -> None:
    assert app.title == "Zivo Library"


def test_health_route() -> None:
    paths = _paths()
    assert "/api/health" in paths


def test_library_routes_mounted() -> None:
    paths = _paths()
    assert any(p.startswith("/api/sources") for p in paths)
    assert any(p.startswith("/api/documents") for p in paths)
    assert any(p.startswith("/api/activities") for p in paths)
    assert any(p.startswith("/api/audiobook") for p in paths)
    assert not any(p.startswith("/api/models") for p in paths)
    assert not any(p.startswith("/api/mcq") for p in paths)
