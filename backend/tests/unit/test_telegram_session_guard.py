"""TELEGRAM_SESSION path traversal / StringSession resolution."""

from __future__ import annotations

import pytest


def test_resolve_rejects_path_separators(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStringSession:
        def __init__(self, raw: str) -> None:
            self.raw = raw

    import types
    import sys

    fake_sessions = types.ModuleType("telethon.sessions")
    fake_sessions.StringSession = FakeStringSession  # type: ignore[attr-defined]
    fake_telethon = types.ModuleType("telethon")
    fake_telethon.sessions = fake_sessions  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telethon", fake_telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", fake_sessions)

    from run_newspaper_ingest import resolve_telegram_session

    with pytest.raises(SystemExit) as exc:
        resolve_telegram_session("../secrets/tele")
    assert "TELEGRAM_SESSION" in str(exc.value)

    with pytest.raises(SystemExit):
        resolve_telegram_session("/tmp/tele")

    assert resolve_telegram_session("zivo_news") == "zivo_news"
    ss = resolve_telegram_session("1" + ("A" * 100))
    assert isinstance(ss, FakeStringSession)
