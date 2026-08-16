#!/usr/bin/env python3
"""Run auth Alembic under a Postgres advisory lock (production migrate job)."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

AUTH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTH_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.engine_runtime import Pred, Rule, apply, first_match, pick  # noqa: E402

LOCK_NAME = "zivo:auth-alembic-migration"


def _lock_id(name: str) -> int:
    value = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")
    return pick(value >= 2**63, lambda: value - 2**64, lambda: value)


def _run_alembic(revision: str, timeout_seconds: int) -> int:
    command = [sys.executable, "-m", "alembic", "upgrade", revision]
    print(f"Running {' '.join(command)} with timeout={timeout_seconds}s", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=AUTH_ROOT,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"Alembic timed out after {timeout_seconds}s", file=sys.stderr, flush=True)
        return 124
    return result.returncode


def _print_fail(result: subprocess.CompletedProcess) -> int:
    print(result.stderr or result.stdout, file=sys.stderr, flush=True)
    return result.returncode


def _verify_head(timeout_seconds: int) -> int:
    command = [sys.executable, "-m", "alembic", "current"]
    print(f"Verifying Alembic head via {' '.join(command)}", flush=True)
    try:
        current_result = subprocess.run(
            command,
            cwd=AUTH_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("Alembic current timed out", file=sys.stderr, flush=True)
        return 124

    def _after_current() -> int:
        heads_result = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=AUTH_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

        def _after_heads() -> int:
            current = next(
                (line.split()[0] for line in filter(str.strip, current_result.stdout.splitlines())),
                "",
            )
            heads = [line.split()[0] for line in filter(str.strip, heads_result.stdout.splitlines())]
            unique_heads = sorted(set(heads))

            def _check_match() -> int:
                head = unique_heads[0]

                def _ok() -> int:
                    print(f"Database revision matches Alembic head: {head}", flush=True)
                    return 0

                def _mismatch() -> int:
                    print(
                        f"Database revision {current!r} does not match Alembic head {head!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 1

                return pick(current != head, _mismatch, _ok)

            def _bad_heads() -> int:
                print(
                    f"Expected exactly one Alembic head, found {len(unique_heads)}: {unique_heads}",
                    file=sys.stderr,
                    flush=True,
                )
                return 1

            return pick(len(unique_heads) != 1, _bad_heads, _check_match)

        return pick(heads_result.returncode != 0, lambda: _print_fail(heads_result), _after_heads)

    return pick(current_result.returncode != 0, lambda: _print_fail(current_result), _after_current)


def _acquire_lock(connection, lock_id: int, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds

    def go() -> bool:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": lock_id},
        ).scalar()
        remaining = int(deadline - time.monotonic())

        def _wait() -> bool:
            print(
                f"Migration lock {lock_id} is held by another session; waiting up to {remaining}s",
                flush=True,
            )
            time.sleep(min(5, remaining))
            return go()

        return apply(
            first_match(
                (
                    Rule(when=(Pred("acquired", "truthy"),), action="got"),
                    Rule(when=(Pred("expired", "truthy"),), action="timeout"),
                    Rule(when=(), action="wait"),
                ),
                {"acquired": bool(acquired), "expired": remaining <= 0},
            ).action,
            {
                "got": lambda: True,
                "timeout": lambda: False,
                "wait": _wait,
            },
        )

    return go()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="head", help="Alembic revision target.")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--lock-timeout-seconds", type=int, default=120)
    parser.add_argument("--verify-head", action="store_true")
    args = parser.parse_args()

    def _bad_timeout() -> int:
        print("Timeout values must be positive integers", file=sys.stderr)
        return 2

    def _run() -> int:
        database_url = os.getenv("DATABASE_URL") or get_settings().database_url

        def _missing_url() -> int:
            print("DATABASE_URL is not set", file=sys.stderr)
            return 2

        def _with_url() -> int:
            lock_id = _lock_id(LOCK_NAME)
            engine = create_engine(database_url, poolclass=NullPool)

            with engine.connect() as connection:
                print(
                    f"Acquiring Postgres advisory lock {lock_id} with timeout={args.lock_timeout_seconds}s",
                    flush=True,
                )

                def _locked() -> int:
                    try:
                        exit_code = _run_alembic(args.revision, args.timeout_seconds)

                        def _after_alembic() -> int:
                            return pick(
                                args.verify_head,
                                lambda: _verify_head(min(args.timeout_seconds, 120)),
                                lambda: 0,
                            )

                        return pick(exit_code != 0, lambda: exit_code, _after_alembic)
                    finally:
                        print(f"Releasing Postgres advisory lock {lock_id}", flush=True)
                        released = connection.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"),
                            {"lock_id": lock_id},
                        ).scalar()
                        pick(
                            not released,
                            lambda: print(
                                f"Warning: advisory lock {lock_id} was not held during release",
                                file=sys.stderr,
                                flush=True,
                            ),
                            lambda: None,
                        )

                def _timeout() -> int:
                    print(
                        f"Timed out waiting for migration advisory lock {lock_id}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 75

                return pick(
                    not _acquire_lock(connection, lock_id, args.lock_timeout_seconds),
                    _timeout,
                    _locked,
                )

        return pick(not database_url, _missing_url, _with_url)

    return pick(
        args.timeout_seconds <= 0 or args.lock_timeout_seconds <= 0,
        _bad_timeout,
        _run,
    )


def _cli() -> None:
    raise SystemExit(main())


pick(__name__ == "__main__", _cli, lambda: None)
