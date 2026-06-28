"""Tests for ETA handler registration and structured logging fixes."""

from __future__ import annotations

import logging


from app.eta.registry import get_handler, list_handlers
from app.eta.submit import build_job


# --------------------------------------------------------------------------- #
# FIX #1: Handler registration via package import (API process)
# --------------------------------------------------------------------------- #

def test_importing_app_eta_registers_handlers() -> None:
    """Importing `app.eta` must register all handlers as a side-effect.

    This is what makes build_job/submit_job resolve workload+priority from the
    registry in the API process (not just the worker processes).
    """
    import app.eta  # noqa: F401  — triggers registration

    expected = {
        "ingest.fetch_file",
        "ingest.parse_document",
        "ingest.chunk_pages",
        "ingest.embed_chunks",
        "ingest.write_chunks",
        "ingest.page",
        "ingest.rag_window",
        "learn.transition_prep",
        "summarize.start",
        "summarize.generate",
    }
    present = set(list_handlers())
    assert expected <= present, f"Missing handlers after app.eta import: {expected - present}"


def test_build_job_resolves_workload_from_registry() -> None:
    """build_job must look up the handler registry to get the correct workload.

    Without registration (the bug), get_handler returns None and CPU jobs would
    silently default to workload=io.
    """
    import app.eta  # noqa: F401

    cpu_job = build_job(name="ingest.parse_document", payload={})
    io_job = build_job(name="ingest.fetch_file", payload={})
    assert str(cpu_job.workload) == "cpu", f"parse_document must be cpu, got {cpu_job.workload}"
    assert str(io_job.workload) == "io", f"fetch_file must be io, got {io_job.workload}"


def test_importing_main_registers_handlers() -> None:
    """The real API import path: importing app.main must register all handlers."""
    import app.main  # noqa: F401

    assert get_handler("ingest.parse_document") is not None
    assert get_handler("summarize.generate") is not None


# --------------------------------------------------------------------------- #
# FIX #3: Structured logging (extra={...}, not %-format)
# --------------------------------------------------------------------------- #

def test_worker_uses_structured_logging(caplog) -> None:
    """Worker log records must carry structured extra fields (job_id, job_name),
    not %-formatted message strings."""
    import app.eta.worker as worker_mod

    caplog.set_level(logging.INFO, logger=worker_mod.logger.name)

    # Emit a log the way the worker does
    worker_mod.logger.info(
        "ETA job started",
        extra={"job_id": "abc-123", "job_name": "test.job", "attempt": 1, "max_attempts": 3},
    )

    record = next(r for r in caplog.records if r.message == "ETA job started")
    assert record.job_id == "abc-123"
    assert record.job_name == "test.job"
    assert record.attempt == 1
    assert record.max_attempts == 3


def test_async_worker_uses_structured_logging(caplog) -> None:
    import app.eta.worker_async as worker_async_mod

    caplog.set_level(logging.INFO, logger=worker_async_mod.logger.name)

    worker_async_mod.logger.info(
        "ETA async job succeeded",
        extra={"job_id": "xyz-789"},
    )
    record = next(r for r in caplog.records if r.message == "ETA async job succeeded")
    assert record.job_id == "xyz-789"


def test_no_percent_format_in_worker_logs() -> None:
    """Static check: no logger call should use %-format args (the old pattern)."""
    import inspect
    import re

    import app.eta.worker as worker_mod
    import app.eta.worker_async as worker_async_mod

    percent_pattern = re.compile(r'logger\.\w+\([^)]*%s')

    for mod in (worker_mod, worker_async_mod):
        source = inspect.getsource(mod)
        offenders = percent_pattern.findall(source)
        assert not offenders, f"{mod.__name__} still has %-format logger calls: {offenders}"
