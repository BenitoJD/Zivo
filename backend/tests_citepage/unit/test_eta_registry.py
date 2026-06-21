"""Unit tests for the ETA handler registry (no DB required)."""

from pydantic import BaseModel

from app.eta.registry import (
    EtaHandler,
    eta,
    get_handler,
    list_handlers,
    normalize_payload,
    normalize_result,
)
from app.models import JobPriority, JobWorkload


class _SampleInput(BaseModel):
    document_id: str


class _SampleOutput(BaseModel):
    ok: bool


def test_eta_decorator_registers_handler() -> None:
    @eta(name="test.register_check", workload=JobWorkload.io, priority=JobPriority.HIGH)
    def fn(payload):
        return payload

    handler = get_handler("test.register_check")
    assert handler is not None
    assert handler.name == "test.register_check"
    assert handler.workload == JobWorkload.io
    assert handler.priority == JobPriority.HIGH
    assert handler.fn is fn


def test_eta_decorator_defaults() -> None:
    @eta(name="test.defaults")
    def fn(payload):
        return payload

    handler = get_handler("test.defaults")
    assert handler is not None
    assert handler.workload == JobWorkload.io  # default workload
    assert handler.priority == JobPriority.MEDIUM  # default priority


def test_get_handler_returns_none_for_unknown() -> None:
    """Critical: unknown names must return None, NOT raise — the worker relies on this."""
    assert get_handler("does.not.exist") is None


def test_list_handlers_returns_copy() -> None:
    @eta(name="test.list_copy")
    def fn(payload):
        return payload

    handlers = list_handlers()
    handlers["mutated"] = "x"  # mutating the returned dict must not affect the registry
    assert "mutated" not in list_handlers()


def test_normalize_payload_with_input_model() -> None:
    handler = EtaHandler(
        name="x", fn=lambda p: p, input_model=_SampleInput, output_model=None,
        workload=JobWorkload.io, priority=JobPriority.MEDIUM,
    )
    result = normalize_payload(handler, {"document_id": "abc"})
    assert result == {"document_id": "abc"}


def test_normalize_payload_without_handler_returns_raw() -> None:
    result = normalize_payload(None, {"foo": "bar"})
    assert result == {"foo": "bar"}


def test_normalize_result_with_output_model() -> None:
    handler = EtaHandler(
        name="x", fn=lambda p: p, input_model=None, output_model=_SampleOutput,
        workload=JobWorkload.io, priority=JobPriority.MEDIUM,
    )
    result = normalize_result(handler, {"ok": True})
    assert result == {"ok": True}


def test_normalize_result_returns_raw_for_plain_dict() -> None:
    assert normalize_result(None, {"a": 1}) == {"a": 1}


def test_existing_handlers_registered() -> None:
    """The real ingest pipeline handlers must be present after import."""
    from app.eta.handlers import cpu as _cpu  # noqa: F401
    from app.eta.handlers import io as _io  # noqa: F401

    expected = {
        "ingest.fetch_file",
        "ingest.parse_document",
        "ingest.chunk_pages",
        "ingest.embed_chunks",
        "ingest.write_chunks",
        "summarize.start",
        "summarize.generate",
    }
    present = set(list_handlers())
    missing = expected - present
    assert not missing, f"Missing registered handlers: {missing}"
