"""Slim Docker trees drop unused FastAPI modules and sibling HTTP packages."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "slim_service_tree.py"
BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
HTTP_DOCKERFILES = (
    "practice/Dockerfile",
    "content/Dockerfile",
    "study/Dockerfile",
    "library/Dockerfile",
    "admin/Dockerfile",
    "workers/Dockerfile",
)


def _tree(tmp_path: Path) -> None:
    api = tmp_path / "app" / "api"
    api.mkdir(parents=True)
    (api / "__init__.py").write_text("")
    (api / "health.py").write_text("router = None\n")
    (api / "router.py").write_text("from app.api import health\n")
    (api / "sources.py").write_text("x\n")
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "main.py").write_text("from app.api import health\n")
    (tmp_path / "run_eta_worker_async.py").write_text("x\n")
    (tmp_path / "run_eta_worker_cpu.py").write_text("x\n")
    (tmp_path / "run_newspaper_ingest.py").write_text("x\n")
    (tmp_path / "alembic.ini").write_text("x\n")
    (tmp_path / "alembic").mkdir()
    (tmp_path / "alembic" / "env.py").write_text("from app.config import get_settings\n")
    (tmp_path / "app" / "config.py").write_text("def get_settings():\n    return None\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "study_api").mkdir()
    (tmp_path / "study_api" / "mcq.py").write_text("x\n")
    (tmp_path / "practice_main.py").write_text("from app.api import health\n")
    (tmp_path / "practice_api").mkdir()
    (tmp_path / "practice_api" / "__init__.py").write_text("")
    (tmp_path / "practice_api" / "coding.py").write_text("from app.api import health\n")


def test_worker_profile_drops_api_keeps_entrypoints(tmp_path: Path) -> None:
    _tree(tmp_path)
    subprocess.check_call([sys.executable, str(SCRIPT), "worker", "--root", str(tmp_path)])
    assert not (tmp_path / "app" / "api").exists()
    assert not (tmp_path / "app" / "main.py").exists()
    assert (tmp_path / "run_eta_worker_async.py").exists()
    assert (tmp_path / "run_eta_worker_cpu.py").exists()
    assert (tmp_path / "run_newspaper_ingest.py").exists()
    assert not (tmp_path / "alembic.ini").exists()
    assert not (tmp_path / "tests").exists()
    assert not (tmp_path / "frontend").exists()
    assert not (tmp_path / "study_api").exists()
    assert not (tmp_path / "practice_api").exists()


def test_practice_profile_drops_product_alembic_and_siblings(tmp_path: Path) -> None:
    _tree(tmp_path)
    subprocess.check_call([sys.executable, str(SCRIPT), "practice", "--root", str(tmp_path)])
    assert not (tmp_path / "alembic.ini").exists()
    assert not (tmp_path / "alembic").exists()
    assert (tmp_path / "app" / "api" / "health.py").exists()
    assert not (tmp_path / "app" / "api" / "sources.py").exists()
    assert not (tmp_path / "app" / "api" / "router.py").exists()
    assert not (tmp_path / "run_eta_worker_async.py").exists()
    assert not (tmp_path / "frontend").exists()
    assert not (tmp_path / "study_api").exists()
    assert (tmp_path / "practice_api" / "coding.py").exists()
    assert (tmp_path / "practice_main.py").exists()


def test_migrate_profile_keeps_alembic_drops_http(tmp_path: Path) -> None:
    _tree(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run_alembic_with_lock.py").write_text("x\n")
    (tmp_path / "scripts" / "other.py").write_text("x\n")
    subprocess.check_call([sys.executable, str(SCRIPT), "migrate", "--root", str(tmp_path)])
    assert not (tmp_path / "app" / "api").exists()
    assert not (tmp_path / "app" / "main.py").exists()
    assert not (tmp_path / "run_eta_worker_async.py").exists()
    assert (tmp_path / "alembic.ini").exists()
    assert (tmp_path / "alembic" / "env.py").exists()
    assert (tmp_path / "scripts" / "run_alembic_with_lock.py").exists()
    assert not (tmp_path / "scripts" / "other.py").exists()
    assert not (tmp_path / "frontend").exists()
    assert not (tmp_path / "practice_api").exists()


def test_ast_prune_drops_unimported_service(tmp_path: Path) -> None:
    _tree(tmp_path)
    services = tmp_path / "app" / "services"
    services.mkdir()
    (services / "__init__.py").write_text("")
    (services / "keep_me.py").write_text("VALUE = 1\n")
    (services / "drop_me.py").write_text("VALUE = 2\n")
    (tmp_path / "practice_main.py").write_text(
        "from app.api import health\nfrom app.services.keep_me import VALUE\n"
    )
    subprocess.check_call([sys.executable, str(SCRIPT), "practice", "--root", str(tmp_path)])
    assert (services / "keep_me.py").exists()
    assert not (services / "drop_me.py").exists()


def test_practice_real_tree_has_no_sibling_surface(tmp_path: Path) -> None:
    shutil.copytree(BACKEND / "app", tmp_path / "app")
    shutil.copy2(REPO / "practice" / "practice_main.py", tmp_path / "practice_main.py")
    shutil.copytree(REPO / "practice" / "practice_api", tmp_path / "practice_api")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "study_api").mkdir()
    (tmp_path / "study_api" / "mcq.py").write_text("x\n")
    subprocess.check_call([sys.executable, str(SCRIPT), "practice", "--root", str(tmp_path)])
    assert not (tmp_path / "frontend").exists()
    assert not (tmp_path / "study_api").exists()
    assert not (tmp_path / "app" / "api" / "mcq.py").exists()
    assert not (tmp_path / "app" / "api" / "sources.py").exists()
    assert (tmp_path / "practice_api" / "coding.py").exists()
    assert (tmp_path / "app" / "api" / "health.py").exists()
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    subprocess.check_call(
        [
            sys.executable,
            "-c",
            "from practice_main import app; assert app.title == 'Zivo Practice'",
        ],
        cwd=tmp_path,
        env=env,
    )


def test_worker_real_tree_has_no_http_packages(tmp_path: Path) -> None:
    shutil.copytree(BACKEND / "app", tmp_path / "app")
    workers = REPO / "workers"
    for name in (
        "run_eta_worker_async.py",
        "run_eta_worker_cpu.py",
        "run_newspaper_ingest.py",
    ):
        shutil.copy2(workers / name, tmp_path / name)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "practice_api").mkdir()
    (tmp_path / "practice_api" / "coding.py").write_text("x\n")
    subprocess.check_call([sys.executable, str(SCRIPT), "worker", "--root", str(tmp_path)])
    assert not (tmp_path / "app" / "api").exists()
    assert not (tmp_path / "app" / "main.py").exists()
    assert not (tmp_path / "frontend").exists()
    assert not (tmp_path / "practice_api").exists()
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    subprocess.check_call(
        [
            sys.executable,
            "-c",
            "from app.eta.worker import run_eta_worker; import sys; "
            "loaded = [m for m in sys.modules if m == 'app.api' or m.startswith('app.api.') "
            "or m.split('.')[0] in {'practice_api','study_api','library_api','content_api','admin_api'}]; "
            "assert not loaded, loaded; assert callable(run_eta_worker)",
        ],
        cwd=tmp_path,
        env=env,
    )


@pytest.mark.parametrize(
    ("rel", "forbidden"),
    [
        ("auth/Dockerfile", "COPY backend/"),
        ("storage/Dockerfile", "COPY backend/"),
        ("auth/Dockerfile", "frontend"),
        ("storage/Dockerfile", "frontend"),
    ],
)
def test_dedicated_packages_do_not_copy_backend_or_frontend(rel: str, forbidden: str) -> None:
    text = (REPO / rel).read_text()
    assert forbidden not in text


@pytest.mark.parametrize("rel", HTTP_DOCKERFILES)
def test_http_and_worker_images_do_not_copy_whole_backend(rel: str) -> None:
    text = (REPO / rel).read_text()
    assert "COPY backend/ /app/" not in text
    assert "COPY backend/app" in text


def test_migrate_image_has_no_uvicorn_and_no_whole_tree_copy() -> None:
    text = (REPO / "backend" / "Dockerfile").read_text()
    assert "COPY backend/ /app/" not in text
    assert "uvicorn" not in text
    assert "run_alembic_with_lock.py" in text
    assert "slim_service_tree.py migrate" in text


def test_next_has_no_zivo_api_catchall() -> None:
    text = (REPO / "frontend" / "next.config.ts").read_text()
    assert 'source: "/api/:path*"' not in text
    assert "zivo-api" not in text
    assert "API_PROXY_URL" not in text
    assert 'source: "/health"' in text
