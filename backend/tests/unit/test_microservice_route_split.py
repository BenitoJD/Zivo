"""Product HTTP lives in owned packages; backend/app is a library, not a process."""

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
API_DIR = BACKEND / "app" / "api"


def test_product_api_entrypoint_is_gone() -> None:
    assert not (BACKEND / "app" / "main.py").exists()
    assert not (API_DIR / "router.py").exists()


def test_backend_api_package_is_shared_health_only() -> None:
    names = {p.name for p in API_DIR.iterdir() if p.suffix == ".py"}
    assert names == {"__init__.py", "health.py"}


def test_owned_route_packages_exist() -> None:
    repo = BACKEND.parent
    assert (repo / "practice" / "practice_api" / "coding.py").is_file()
    assert (repo / "content" / "content_api" / "seo_learn.py").is_file()
    assert (repo / "study" / "study_api" / "mcq.py").is_file()
    assert (repo / "library" / "library_api" / "documents.py").is_file()
    assert (repo / "admin" / "admin_api" / "models.py").is_file()
