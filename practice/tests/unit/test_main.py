"""Process smoke: this service is a FastAPI with the expected title and routes."""

from practice_main import app


def _paths() -> set[str]:
    return set(app.openapi()["paths"])


def test_title() -> None:
    assert app.title == "Zivo Practice"


def test_health_route() -> None:
    paths = _paths()
    assert "/health" in paths or "/api/health" in paths
    assert "/api/health" in paths


def test_practice_routes_mounted() -> None:
    paths = _paths()
    assert any(p.startswith("/api/coding") for p in paths)
    assert any(p.startswith("/api/system-design") for p in paths)
    assert any(p.startswith("/api/practice") for p in paths)
    assert any(p.startswith("/api/newspaper") for p in paths)
    assert not any("/admin/channel" in p for p in paths)


def test_does_not_load_sibling_api_packages() -> None:
    import sys

    loaded = [
        m
        for m in sys.modules
        if m.split(".")[0] in {"content_api", "study_api", "library_api", "admin_api"}
    ]
    assert loaded == []
