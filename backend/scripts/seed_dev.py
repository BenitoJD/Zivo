#!/usr/bin/env python3
"""Seed local dev users, LLM registry, and demo document."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/seed_dev.py` from repo root or /app in containers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.engine_runtime import choose, pick
from app.services.dev_seed import seed_local_database


def main() -> int:
    with SessionLocal() as db:
        report = seed_local_database(db, demo_embeddings=False)
    for spec, created in report.users:
        action = choose(created, "created", "updated")
        print(f"user {spec.username!r} {action}")
    pick(
        bool(report.default_chat_model),
        lambda: print(f"default chat model: {report.default_chat_model}"),
        lambda: None,
    )
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _cli() -> None:
    raise SystemExit(main())


pick(__name__ == "__main__", _cli, lambda: None)
