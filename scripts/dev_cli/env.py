from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
AUTH_DIR = ROOT / "auth"
STORAGE_DIR = ROOT / "storage"
PRACTICE_DIR = ROOT / "practice"
CONTENT_DIR = ROOT / "content"
STUDY_DIR = ROOT / "study"
LIBRARY_DIR = ROOT / "library"
ADMIN_DIR = ROOT / "admin"
FRONTEND_DIR = ROOT / "frontend"
BACKEND_DEFAULTS = BACKEND_DIR / ".env.example"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ResolvedEnv:
    backend: dict[str, str]


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_RE.match(key):
            continue
        value = raw_value.strip()
        if value and value[0] in {"'", '"'}:
            try:
                parts = shlex.split(value, posix=True)
                value = parts[0] if parts else ""
            except ValueError:
                value = value.strip("'\"")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def resolve_env(process_env: dict[str, str] | None = None) -> ResolvedEnv:
    proc = dict(process_env if process_env is not None else os.environ)
    backend = parse_env_file(BACKEND_DEFAULTS)
    backend.update(parse_env_file(BACKEND_DIR / ".env.local"))
    backend.update({k: v for k, v in proc.items() if _ENV_KEY_RE.match(k)})
    return ResolvedEnv(backend=backend)


def require_defaults() -> None:
    if not BACKEND_DEFAULTS.exists():
        raise RuntimeError(f"Missing {BACKEND_DEFAULTS.relative_to(ROOT)}")
