#!/usr/bin/env python3
"""Apply all schema migrations via Alembic (K8s job entrypoint alias)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        check=False,
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
