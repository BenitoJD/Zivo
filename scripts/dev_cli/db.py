from __future__ import annotations

import subprocess
from pathlib import Path

from .env import BACKEND_DIR, resolve_env

VENV = Path.home() / ".venv" / "zivo"


def python_bin() -> str:
    candidate = VENV / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError(f"Missing {VENV}. Run ./scripts/dev.sh setup first.")


def database_url() -> str:
    env = resolve_env().backend
    return env.get(
        "DATABASE_URL",
        "postgresql+psycopg://zivo:zivo@localhost:5455/zivo",
    )


def backend_env(port: int | None = None) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(resolve_env().backend)
    env["DATABASE_URL"] = database_url()
    env.setdefault("MINIO_ENDPOINT", "localhost:9020")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def apply_schema(env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(
        [python_bin(), "scripts/apply_intel_schema.py"],
        cwd=BACKEND_DIR,
        env=env or backend_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("schema apply failed")
