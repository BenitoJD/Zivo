#!/usr/bin/env python3
"""Deprecated: use `alembic upgrade head` (runs migration 002_qb_schema)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def main() -> int:
    print("apply_qb_schema.py is deprecated; running alembic upgrade head", file=sys.stderr)
    return subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
    )


if __name__ == "__main__":
    raise SystemExit(main())
