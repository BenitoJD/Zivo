#!/usr/bin/env python3
"""Run storage Alembic under a Postgres advisory lock (production migrate job)."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

STORAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STORAGE_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402

LOCK_NAME = "zivo:storage-alembic-migration"


def _lock_id(name: str) -> int:
    value = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")
    if value >= 2**63:
        value -= 2**64
    return value


def _run_alembic(revision: str, timeout_seconds: int) -> int:
    command = [sys.executable, "-m", "alembic", "upgrade", revision]
    print(f"Running {' '.join(command)} with timeout={timeout_seconds}s", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=STORAGE_ROOT,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"Alembic timed out after {timeout_seconds}s", file=sys.stderr, flush=True)
        return 124
    return result.returncode


def _verify_head(timeout_seconds: int) -> int:
    command = [sys.executable, "-m", "alembic", "current"]
    print(f"Verifying Alembic head via {' '.join(command)}", flush=True)
    try:
        current_result = subprocess.run(
            command,
            cwd=STORAGE_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("Alembic current timed out", file=sys.stderr, flush=True)
        return 124

    if current_result.returncode != 0:
        print(current_result.stderr or current_result.stdout, file=sys.stderr, flush=True)
        return current_result.returncode

    heads_result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=STORAGE_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if heads_result.returncode != 0:
        print(heads_result.stderr or heads_result.stdout, file=sys.stderr, flush=True)
        return heads_result.returncode

    current = next(
        (line.split()[0] for line in current_result.stdout.splitlines() if line.strip()),
        "",
    )
    heads = [line.split()[0] for line in heads_result.stdout.splitlines() if line.strip()]
    unique_heads = sorted(set(heads))

    if len(unique_heads) != 1:
        print(
            f"Expected exactly one Alembic head, found {len(unique_heads)}: {unique_heads}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    head = unique_heads[0]
    if current != head:
        print(
            f"Database revision {current!r} does not match Alembic head {head!r}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"Database revision matches Alembic head: {head}", flush=True)
    return 0


def _acquire_lock(connection, lock_id: int, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": lock_id},
        ).scalar()
        if acquired:
            return True

        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return False

        print(
            f"Migration lock {lock_id} is held by another session; waiting up to {remaining}s",
            flush=True,
        )
        time.sleep(min(5, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="head", help="Alembic revision target.")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--lock-timeout-seconds", type=int, default=120)
    parser.add_argument("--verify-head", action="store_true")
    args = parser.parse_args()

    if args.timeout_seconds <= 0 or args.lock_timeout_seconds <= 0:
        print("Timeout values must be positive integers", file=sys.stderr)
        return 2

    database_url = os.getenv("DATABASE_URL") or get_settings().database_url
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    lock_id = _lock_id(LOCK_NAME)
    engine = create_engine(database_url, poolclass=NullPool)

    with engine.connect() as connection:
        print(
            f"Acquiring Postgres advisory lock {lock_id} with timeout={args.lock_timeout_seconds}s",
            flush=True,
        )
        if not _acquire_lock(connection, lock_id, args.lock_timeout_seconds):
            print(
                f"Timed out waiting for migration advisory lock {lock_id}",
                file=sys.stderr,
                flush=True,
            )
            return 75

        try:
            exit_code = _run_alembic(args.revision, args.timeout_seconds)
            if exit_code != 0:
                return exit_code
            if args.verify_head:
                return _verify_head(min(args.timeout_seconds, 120))
            return 0
        finally:
            print(f"Releasing Postgres advisory lock {lock_id}", flush=True)
            released = connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            ).scalar()
            if not released:
                print(
                    f"Warning: advisory lock {lock_id} was not held during release",
                    file=sys.stderr,
                    flush=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
