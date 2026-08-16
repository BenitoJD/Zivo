"""Process smoke: this service is a FastAPI with the expected title and routes."""

from study_main import app


def _paths() -> set[str]:
    return set(app.openapi()["paths"])


def test_title() -> None:
    assert app.title == "Zivo Study"


def test_health_route() -> None:
    paths = _paths()
    assert "/api/health" in paths


def test_study_routes_mounted() -> None:
    paths = _paths()
    assert any(p.startswith("/api/artifacts") for p in paths)
    assert any(p.startswith("/api/mcq") for p in paths)
    assert any(p.startswith("/api/chat") for p in paths)
    assert any(p.startswith("/api/progress") for p in paths)
    assert any(p.startswith("/api/assertions") for p in paths)
    assert any(p == "/api/guest" or p.startswith("/api/guest") for p in paths)
    assert any(p.startswith("/api/offline") for p in paths)
    assert any(p.startswith("/api/reference") for p in paths)


def test_does_not_load_sibling_api_packages() -> None:
    import sys

    loaded = list(
        filter(
            lambda m: m.split(".")[0]
            in {"practice_api", "content_api", "library_api", "admin_api"},
            sys.modules,
        )
    )
    assert loaded == []
