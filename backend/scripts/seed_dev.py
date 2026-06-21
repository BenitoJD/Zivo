#!/usr/bin/env python3
"""Seed local dev users, LLM registry, and demo document."""

from __future__ import annotations

import sys

from app.db import SessionLocal
from app.services.dev_seed import seed_local_database


def main() -> int:
    with SessionLocal() as db:
        report = seed_local_database(db, demo_embeddings=False)
    for spec, created in report.users:
        action = "created" if created else "updated"
        print(f"user {spec.username!r} {action}")
    if report.default_chat_model:
        print(f"default chat model: {report.default_chat_model}")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
