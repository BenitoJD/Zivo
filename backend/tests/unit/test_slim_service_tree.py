"""Slim Docker trees drop unused FastAPI modules and the wrong process entrypoints."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "slim_service_tree.py"


def _tree(tmp_path: Path) -> None:
    api = tmp_path / "app" / "api"
    api.mkdir(parents=True)
    (api / "health.py").write_text("x\n")
    (api / "router.py").write_text("x\n")
    (api / "sources.py").write_text("x\n")
    (tmp_path / "app" / "main.py").write_text("x\n")
    (tmp_path / "run_eta_worker_async.py").write_text("x\n")
    (tmp_path / "run_eta_worker_cpu.py").write_text("x\n")
    (tmp_path / "run_newspaper_ingest.py").write_text("x\n")
    (tmp_path / "alembic.ini").write_text("x\n")
    (tmp_path / "alembic").mkdir()
    (tmp_path / "tests").mkdir()


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


def test_practice_profile_drops_product_alembic(tmp_path: Path) -> None:
    _tree(tmp_path)
    subprocess.check_call([sys.executable, str(SCRIPT), "practice", "--root", str(tmp_path)])
    assert not (tmp_path / "alembic.ini").exists()
    assert not (tmp_path / "alembic").exists()
    assert (tmp_path / "app" / "api" / "health.py").exists()
    assert not (tmp_path / "app" / "api" / "sources.py").exists()
    assert not (tmp_path / "run_eta_worker_async.py").exists()


def test_api_profile_keeps_health_drops_workers(tmp_path: Path) -> None:
    _tree(tmp_path)
    subprocess.check_call([sys.executable, str(SCRIPT), "api", "--root", str(tmp_path)])
    assert (tmp_path / "app" / "api" / "health.py").exists()
    assert (tmp_path / "app" / "api" / "router.py").exists()
    assert not (tmp_path / "app" / "api" / "sources.py").exists()
    assert (tmp_path / "app" / "main.py").exists()
    assert not (tmp_path / "run_eta_worker_async.py").exists()
    assert (tmp_path / "alembic.ini").exists()
