"""Worker process must not pull FastAPI route modules on import."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent


def _walk_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


_HTTP_PACKAGES = (
    "practice_api",
    "content_api",
    "study_api",
    "library_api",
    "admin_api",
)


def _loaded_http_modules() -> list[str]:
    script = (
        "from app.eta.worker import run_eta_worker\n"
        "import sys\n"
        "loaded = [m for m in sys.modules if m == 'app.api' or m.startswith('app.api.') "
        "or m.split('.')[0] in "
        f"{_HTTP_PACKAGES!r}]\n"
        "print('\\n'.join(loaded), end='')\n"
        "assert callable(run_eta_worker)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in proc.stdout.splitlines() if line]


def test_eta_package_does_not_import_app_api() -> None:
    eta = BACKEND / "app" / "eta"
    offenders: list[str] = []
    for path in eta.rglob("*.py"):
        for name in _walk_imports(path):
            if name == "app.api" or name.startswith("app.api."):
                offenders.append(f"{path.relative_to(BACKEND)}:{name}")
    assert offenders == []


def test_worker_entrypoints_do_not_import_app_api() -> None:
    workers = REPO / "workers"
    paths = [
        workers / "run_eta_worker_async.py",
        workers / "run_eta_worker_cpu.py",
        workers / "run_newspaper_ingest.py",
        BACKEND / "app" / "eta" / "worker.py",
    ]
    for path in paths:
        for imported in _walk_imports(path):
            assert imported != "app.api"
            assert not imported.startswith("app.api.")


def test_importing_worker_does_not_load_app_api() -> None:
    assert _loaded_http_modules() == []


def test_importing_io_worker_does_not_load_http_packages() -> None:
    script = (
        "from app.eta.worker_async import run_eta_worker_async\n"
        "import sys\n"
        "loaded = [m for m in sys.modules if m == 'app.api' or m.startswith('app.api.') "
        "or m.split('.')[0] in "
        f"{_HTTP_PACKAGES!r}]\n"
        "assert not loaded, loaded\n"
        "assert callable(run_eta_worker_async)\n"
    )
    subprocess.check_call([sys.executable, "-c", script], cwd=BACKEND)


def test_io_worker_starts_scheduler() -> None:
    text = (BACKEND / "app" / "eta" / "worker_async.py").read_text()
    assert "eta_scheduler_service.start()" in text
    assert "eta_scheduler_service.stop()" in text
