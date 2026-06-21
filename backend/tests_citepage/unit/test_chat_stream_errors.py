"""Chat stream error events expose only user-safe messages."""

from __future__ import annotations

import json

from app.api.chat import _stream_error_event


def test_stream_error_event_omits_internal_detail() -> None:
    event = _stream_error_event("Tutor is busy. Try again.")
    payload = json.loads(event["data"])

    assert event["event"] == "error"
    assert payload == {"message": "Tutor is busy. Try again."}
