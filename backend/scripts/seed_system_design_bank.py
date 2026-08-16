#!/usr/bin/env python3
"""Seed System Design concepts + classic cases."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.engine_runtime import pick  # noqa: E402
from app.services.system_design import seed_system_design_bank  # noqa: E402


def main() -> None:
    with SessionLocal() as db:
        counts = seed_system_design_bank(db)
    print(f"Seeded system design bank: {counts}")


def _cli() -> None:
    main()


pick(__name__ == "__main__", _cli, lambda: None)
