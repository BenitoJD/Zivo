"""Product API is health + scheduler only; other HTTP left for other processes."""

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
    assert not any(p.startswith("/api/guest") for p in paths)
    assert not any(p.startswith("/api/offline") for p in paths)
    assert not any(p.startswith("/api/reference") for p in paths)


def test_library_and_admin_routes_left_the_product_api() -> None:
    paths = _paths()
    assert not any(p.startswith("/api/sources") for p in paths)
    assert not any(p.startswith("/api/documents") for p in paths)
    assert not any(p.startswith("/api/activities") for p in paths)
    assert not any(p.startswith("/api/audiobook") for p in paths)
    assert not any(p.startswith("/api/models") for p in paths)
    assert not any(p.startswith("/api/debug") for p in paths)


def test_product_api_keeps_health_only() -> None:
    paths = _paths()
    assert "/api/health" in paths
    extra = {
        p
        for p in paths
        if p.startswith("/api/") and p not in {"/api/health", "/api/health/ready"}
    }
    assert extra == set()


def test_backend_api_package_has_no_product_routers() -> None:
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[2] / "app" / "api"
    names = {p.name for p in api_dir.iterdir() if p.suffix == ".py"}
    assert names == {"__init__.py", "health.py", "router.py"}
