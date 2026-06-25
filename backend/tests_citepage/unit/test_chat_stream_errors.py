"""Chat stream error events expose only user-safe messages."""

from __future__ import annotations

import json

from app.api.chat import _stream_error_event


def test_stream_error_event_omits_internal_detail() -> None:
    from app.api.chat import _CHAT_BUSY_MESSAGE

    event = _stream_error_event(_CHAT_BUSY_MESSAGE)
    payload = json.loads(event["data"])

    assert event["event"] == "error"
    assert payload == {"message": _CHAT_BUSY_MESSAGE}
