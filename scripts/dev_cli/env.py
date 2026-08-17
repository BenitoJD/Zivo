from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick  # noqa: E402

BACKEND_DIR = ROOT / "backend"
AUTH_DIR = ROOT / "auth"
STORAGE_DIR = ROOT / "storage"
PRACTICE_DIR = ROOT / "practice"
CONTENT_DIR = ROOT / "content"
STUDY_DIR = ROOT / "study"
LIBRARY_DIR = ROOT / "library"
ADMIN_DIR = ROOT / "admin"
WORKERS_DIR = ROOT / "workers"
FRONTEND_DIR = ROOT / "frontend"
BACKEND_DEFAULTS = BACKEND_DIR / ".env.example"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ResolvedEnv:
    backend: dict[str, str]


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}

    def _parse() -> None:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()

            def _export() -> str:
                return line[len("export ") :].lstrip()

            line = pick(line.startswith("export "), _export, lambda: line)

            def _kv() -> None:
                key, raw_value = line.split("=", 1)
                key = key.strip()

                def _value() -> None:
                    value = raw_value.strip()

                    def _quoted() -> str:
                        try:
                            parts = shlex.split(value, posix=True)
                            return pick(bool(parts), lambda: parts[0], lambda: "")
                        except ValueError:
                            return value.strip("'\"")

                    def _plain() -> str:
                        return re.split(r"\s+#", value, maxsplit=1)[0].strip()

                    values[key] = pick(
                        bool(value) and value[0] in {"'", '"'},
                        _quoted,
                        _plain,
                    )

                pick(bool(_ENV_KEY_RE.match(key)), _value, lambda: None)

            pick(not line or line.startswith("#") or "=" not in line, lambda: None, _kv)

    pick(not path.exists(), lambda: None, _parse)
    return values


def resolve_env(process_env: dict[str, str] | None = None) -> ResolvedEnv:
    proc = dict(pick(process_env is not None, lambda: process_env, lambda: os.environ))
    backend = parse_env_file(BACKEND_DEFAULTS)
    backend.update(parse_env_file(BACKEND_DIR / ".env.local"))
    backend.update({k: v for k, v in filter(lambda kv: _ENV_KEY_RE.match(kv[0]), proc.items())})
    return ResolvedEnv(backend=backend)


def _raise(exc: BaseException) -> None:
    raise exc


def require_defaults() -> None:
    pick(
        not BACKEND_DEFAULTS.exists(),
        lambda: _raise(RuntimeError(f"Missing {BACKEND_DEFAULTS.relative_to(ROOT)}")),
        lambda: None,
    )
