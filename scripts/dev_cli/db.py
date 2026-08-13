from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .env import AUTH_DIR, BACKEND_DIR, resolve_env

VENV = Path.home() / ".venv" / "zivo"


@dataclass(frozen=True)
class DatabaseInfo:
    url: str
    name: str
    host: str


def python_bin() -> str:
    candidate = VENV / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError(f"Missing {VENV}. Run ./scripts/dev.sh setup first.")


def database_url() -> str:
    return resolve_env().backend.get(
        "DATABASE_URL",
        "postgresql+psycopg://zivo:zivo@localhost:5455/zivo",
    )


def validate_local_database_url(url: str) -> DatabaseInfo:
    parsed = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    host = parsed.hostname or "localhost"
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"Refusing to run db commands against non-local host: {host}")
    name = (parsed.path or "/zivo").lstrip("/") or "zivo"
    return DatabaseInfo(url=url, name=name, host=host)


def backend_env(port: int | None = None) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(resolve_env().backend)
    env["DATABASE_URL"] = database_url()
    env.setdefault("MINIO_ENDPOINT", "localhost:9020")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=BACKEND_DIR, env=env or backend_env(), text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed")


def run_auth_alembic(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = [python_bin(), "-m", "alembic", *args]
    print("+", " ".join(cmd), "(auth)")
    proc = subprocess.run(cmd, cwd=AUTH_DIR, env=env or backend_env(), text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"auth alembic {' '.join(args)} failed")


def apply_schema(env: dict[str, str] | None = None) -> None:
    """Apply auth then product schema migrations via Alembic."""
    run_auth_alembic("upgrade", "head", env=env)
    run_alembic("upgrade", "head", env=env)


def apply_qb_schema(env: dict[str, str] | None = None) -> None:
    """Alias for apply_schema — qb tables ship in the same Alembic chain."""
    apply_schema(env)
