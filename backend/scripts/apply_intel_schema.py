#!/usr/bin/env python3
"""Deprecated: use `alembic upgrade head` (runs migration 001_intel_foundation)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def main() -> int:
    print("apply_intel_schema.py is deprecated; running alembic upgrade 001_intel_foundation", file=sys.stderr)
    return subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "001_intel_foundation"],
        cwd=BACKEND,
    )


if __name__ == "__main__":
    raise SystemExit(main())
