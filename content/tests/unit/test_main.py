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


def test_does_not_load_sibling_api_packages() -> None:
    import sys

    loaded = [
        m
        for m in sys.modules
        if m.split(".")[0] in {"practice_api", "study_api", "library_api", "admin_api"}
    ]
    assert loaded == []
