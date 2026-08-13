"""Worker process must not pull FastAPI route modules on import."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _walk_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_eta_package_does_not_import_app_api() -> None:
    eta = BACKEND / "app" / "eta"
    offenders: list[str] = []
    for path in eta.rglob("*.py"):
        for name in _walk_imports(path):
            if name == "app.api" or name.startswith("app.api."):
                offenders.append(f"{path.relative_to(BACKEND)}:{name}")
    assert offenders == []


def test_worker_entrypoints_do_not_import_app_api() -> None:
    for name in (
        "run_eta_worker_async.py",
        "run_eta_worker_cpu.py",
        "run_newspaper_ingest.py",
        "app/eta/worker.py",
    ):
        path = BACKEND / name
        for imported in _walk_imports(path):
            assert imported != "app.api"
            assert not imported.startswith("app.api.")


def test_importing_worker_does_not_load_app_api() -> None:
    script = (
        "from app.eta.worker import run_eta_worker\n"
        "import sys\n"
        "loaded = [m for m in sys.modules if m == 'app.api' or m.startswith('app.api.')]\n"
        "assert not loaded, loaded\n"
        "assert callable(run_eta_worker)\n"
    )
    subprocess.check_call([sys.executable, "-c", script], cwd=BACKEND)
