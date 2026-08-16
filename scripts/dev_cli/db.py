from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .env import AUTH_DIR, BACKEND_DIR, CONTENT_DIR, LIBRARY_DIR, ADMIN_DIR, PRACTICE_DIR, STORAGE_DIR, STUDY_DIR, _raise, pick, resolve_env

VENV = Path.home() / ".venv" / "zivo"


@dataclass(frozen=True)
class DatabaseInfo:
    url: str
    name: str
    host: str


def python_bin() -> str:
    candidate = VENV / "bin" / "python"
    return pick(
        candidate.exists(),
        lambda: str(candidate),
        lambda: _raise(RuntimeError(f"Missing {VENV}. Run ./scripts/dev.sh setup first.")),
    )


def database_url() -> str:
    return resolve_env().backend.get(
        "DATABASE_URL",
        "postgresql+psycopg://zivo:zivo@localhost:5455/zivo",
    )


def validate_local_database_url(url: str) -> DatabaseInfo:
    parsed = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    host = parsed.hostname or "localhost"
    pick(
        host not in {"localhost", "127.0.0.1", "::1"},
        lambda: _raise(RuntimeError(f"Refusing to run db commands against non-local host: {host}")),
        lambda: None,
    )
    name = (parsed.path or "/zivo").lstrip("/") or "zivo"
    return DatabaseInfo(url=url, name=name, host=host)


def backend_env(port: int | None = None) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(resolve_env().backend)
    env["DATABASE_URL"] = database_url()
    env.setdefault("MINIO_ENDPOINT", "localhost:9020")
    env.setdefault("STORAGE_URL", "http://127.0.0.1:8202")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _require_ok(proc: subprocess.CompletedProcess, message: str) -> None:
    pick(proc.returncode != 0, lambda: _raise(RuntimeError(message)), lambda: None)


def run_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=BACKEND_DIR, env=env or backend_env(), text=True)
    _require_ok(proc, f"alembic {' '.join(args)} failed")


def run_auth_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(auth)")
    proc = subprocess.run(cmd, cwd=AUTH_DIR, env=env or backend_env(), text=True)
    _require_ok(proc, f"auth alembic {' '.join(args)} failed")


def run_storage_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(storage)")
    proc = subprocess.run(cmd, cwd=STORAGE_DIR, env=env or backend_env(), text=True)
    _require_ok(proc, f"storage alembic {' '.join(args)} failed")


def _product_service_env(service_dir: Path, env: dict[str, str] | None) -> dict[str, str]:
    merged = dict(env or backend_env())
    existing = merged.get("PYTHONPATH", "")
    parts = [str(BACKEND_DIR), str(service_dir)]
    pick(bool(existing), lambda: parts.append(existing), lambda: None)
    merged["PYTHONPATH"] = ":".join(parts)
    return merged


def run_practice_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(practice)")
    proc = subprocess.run(cmd, cwd=PRACTICE_DIR, env=_product_service_env(PRACTICE_DIR, env), text=True)
    _require_ok(proc, f"practice alembic {' '.join(args)} failed")


def run_content_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(content)")
    proc = subprocess.run(cmd, cwd=CONTENT_DIR, env=_product_service_env(CONTENT_DIR, env), text=True)
    _require_ok(proc, f"content alembic {' '.join(args)} failed")


def run_study_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(study)")
    proc = subprocess.run(cmd, cwd=STUDY_DIR, env=_product_service_env(STUDY_DIR, env), text=True)
    _require_ok(proc, f"study alembic {' '.join(args)} failed")


def run_library_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(library)")
    proc = subprocess.run(cmd, cwd=LIBRARY_DIR, env=_product_service_env(LIBRARY_DIR, env), text=True)
    _require_ok(proc, f"library alembic {' '.join(args)} failed")


def run_admin_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(admin)")
    proc = subprocess.run(cmd, cwd=ADMIN_DIR, env=_product_service_env(ADMIN_DIR, env), text=True)
    _require_ok(proc, f"admin alembic {' '.join(args)} failed")


def apply_schema(env: dict[str, str] | None = None) -> None:
    """Apply auth then storage then product schema migrations via Alembic."""
    run_auth_alembic("upgrade", "head", env=env)
    run_storage_alembic("upgrade", "head", env=env)
    run_practice_alembic("upgrade", "head", env=env)
    run_content_alembic("upgrade", "head", env=env)
    run_study_alembic("upgrade", "head", env=env)
    run_library_alembic("upgrade", "head", env=env)
    run_admin_alembic("upgrade", "head", env=env)
    run_alembic("upgrade", "head", env=env)


def apply_qb_schema(env: dict[str, str] | None = None) -> None:
    """Alias for apply_schema — qb tables ship in the same Alembic chain."""
    apply_schema(env)
