#!/usr/bin/env python3
"""Run study Alembic under a Postgres advisory lock (production migrate job)."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))
_BACKEND = SERVICE_ROOT.parent / "backend"


def _boot_pick(flag: bool, when_true, when_false):
    return {True: when_true, False: when_false}[bool(flag)]()


_boot_pick(_BACKEND.is_dir(), lambda: sys.path.insert(0, str(_BACKEND)), lambda: None)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.engine_runtime import Pred, Rule, apply, first_match, pick  # noqa: E402

LOCK_NAME = "zivo:study-alembic-migration"


def _raise(exc: BaseException) -> None:
    raise exc


def _lock_id(name: str) -> int:
    value = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")
    return pick(value >= 2**63, lambda: value - 2**64, lambda: value)


def _alembic_env() -> dict[str, str]:
    env = dict(os.environ)
    parts = pick(
        _BACKEND.is_dir(),
        lambda: [str(_BACKEND), str(SERVICE_ROOT)],
        lambda: [str(SERVICE_ROOT)],
    )
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = ":".join(parts + pick(bool(existing), lambda: [existing], lambda: []))
    return env


def _run_alembic(revision: str, timeout_seconds: int) -> int:
    command = [sys.executable, "-m", "alembic", "upgrade", revision]
    print(f"Running {' '.join(command)} with timeout={timeout_seconds}s", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=SERVICE_ROOT,
            env=_alembic_env(),
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
            cwd=SERVICE_ROOT,
            env=_alembic_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("Alembic current timed out", file=sys.stderr, flush=True)
        return 124

    def _fail_current() -> int:
        print(current_result.stderr or current_result.stdout, file=sys.stderr, flush=True)
        return current_result.returncode

    def _heads() -> int:
        heads_result = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=SERVICE_ROOT,
            env=_alembic_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

        def _fail_heads() -> int:
            print(heads_result.stderr or heads_result.stdout, file=sys.stderr, flush=True)
            return heads_result.returncode

        def _compare() -> int:
            current = next(
                map(
                    lambda line: line.split()[0],
                    filter(str.strip, current_result.stdout.splitlines()),
                ),
                "",
            )
            heads = list(
                map(
                    lambda line: line.split()[0],
                    filter(str.strip, heads_result.stdout.splitlines()),
                )
            )
            unique_heads = sorted(set(heads))

            def _bad_count() -> int:
                print(
                    f"Expected exactly one Alembic head, found {len(unique_heads)}: {unique_heads}",
                    file=sys.stderr,
                    flush=True,
                )
                return 1

            def _check_head() -> int:
                head = unique_heads[0]
                return pick(
                    current != head,
                    lambda: (
                        print(
                            f"Database revision {current!r} does not match Alembic head {head!r}",
                            file=sys.stderr,
                            flush=True,
                        )
                        or 1
                    ),
                    lambda: (
                        print(f"Database revision matches Alembic head: {head}", flush=True) or 0
                    ),
                )

            return pick(len(unique_heads) != 1, _bad_count, _check_head)

        return pick(heads_result.returncode != 0, _fail_heads, _compare)

    return pick(current_result.returncode != 0, _fail_current, _heads)


def _acquire_lock(connection, lock_id: int, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds

    def attempt() -> bool:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            ).scalar()
        )
        remaining = int(deadline - time.monotonic())
        hit = first_match(
            (
                Rule(when=(Pred("acquired", "truthy"),), action="ok"),
                Rule(when=(Pred("remaining", "lte", 0),), action="timeout"),
                Rule(when=(), action="wait"),
            ),
            {"acquired": acquired, "remaining": remaining},
        )

        def _wait() -> bool:
            print(
                f"Migration lock {lock_id} is held by another session; waiting up to {remaining}s",
                flush=True,
            )
            time.sleep(min(5, remaining))
            return attempt()

        return apply(
            hit.action,
            {
                "ok": lambda: True,
                "timeout": lambda: False,
                "wait": _wait,
            },
        )

    return attempt()


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

        def _no_url() -> int:
            print("DATABASE_URL is not set", file=sys.stderr)
            return 2

        def _locked() -> int:
            lock_id = _lock_id(LOCK_NAME)
            engine = create_engine(database_url, poolclass=NullPool)

            with engine.connect() as connection:
                print(
                    f"Acquiring Postgres advisory lock {lock_id} with timeout={args.lock_timeout_seconds}s",
                    flush=True,
                )

                def _timeout() -> int:
                    print(
                        f"Timed out waiting for migration advisory lock {lock_id}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 75

                def _migrate() -> int:
                    try:
                        exit_code = _run_alembic(args.revision, args.timeout_seconds)
                        return pick(
                            exit_code != 0,
                            lambda: exit_code,
                            lambda: pick(
                                args.verify_head,
                                lambda: _verify_head(min(args.timeout_seconds, 120)),
                                lambda: 0,
                            ),
                        )
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

                return pick(
                    not _acquire_lock(connection, lock_id, args.lock_timeout_seconds),
                    _timeout,
                    _migrate,
                )

        return pick(not database_url, _no_url, _locked)

    return pick(
        args.timeout_seconds <= 0 or args.lock_timeout_seconds <= 0,
        _bad_timeout,
        _run,
    )


pick(__name__ == "__main__", lambda: _raise(SystemExit(main())), lambda: None)
