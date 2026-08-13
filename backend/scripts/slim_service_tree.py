#!/usr/bin/env python3
"""Strip a copied backend tree down to one process's code.

Used by Dockerfiles after ``COPY backend/``. Profiles drop FastAPI route modules
the process does not serve, plus worker/API entrypoints the process does not run.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

API_KEEP: dict[str, set[str]] = {
    "worker": set(),
    "api": {"__init__.py", "health.py", "router.py"},
    "practice": {
        "__init__.py",
        "access.py",
        "coding.py",
        "health.py",
        "newspaper.py",
        "practice.py",
        "system_design.py",
    },
    "content": {"__init__.py", "health.py", "newspaper_admin.py", "seo_learn.py"},
    "study": {
        "__init__.py",
        "access.py",
        "artifacts.py",
        "assertions.py",
        "chat.py",
        "guest.py",
        "health.py",
        "learn.py",
        "mcq.py",
        "offline.py",
        "progress.py",
        "reference.py",
        "study.py",
        "topics.py",
    },
    "library": {
        "__init__.py",
        "access.py",
        "activities.py",
        "audiobook.py",
        "documents.py",
        "health.py",
        "sources.py",
    },
    "admin": {"__init__.py", "debug.py", "health.py", "models.py"},
}

WORKER_ENTRYPOINTS = (
    "run_eta_worker_async.py",
    "run_eta_worker_cpu.py",
    "run_newspaper_ingest.py",
)

DROP_DIRS = ("tests", ".pytest_cache", ".ruff_cache", "__pycache__")


def _rm(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def slim(root: Path, profile: str) -> None:
    if profile not in API_KEEP:
        raise SystemExit(f"unknown profile {profile!r}; choose from {sorted(API_KEEP)}")

    api_dir = root / "app" / "api"
    keep = API_KEEP[profile]
    if api_dir.is_dir():
        if not keep:
            _rm(api_dir)
        else:
            for child in api_dir.iterdir():
                if child.name not in keep:
                    _rm(child)

    if profile != "api":
        _rm(root / "app" / "main.py")
        _rm(root / "alembic")
        _rm(root / "alembic.ini")

    if profile != "worker":
        for name in WORKER_ENTRYPOINTS:
            _rm(root / name)

    for name in DROP_DIRS:
        _rm(root / name)

    print(f"slimmed {root} as {profile}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(API_KEEP))
    parser.add_argument("--root", default=".", help="Copied backend tree (default cwd)")
    args = parser.parse_args()
    slim(Path(args.root).resolve(), args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
