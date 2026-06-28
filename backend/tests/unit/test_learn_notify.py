"""Regression: the learn NOTIFY channel must be safe to use as an identifier.

The bug: `LISTEN zivo_learn_<uuid>` was emitted as an f-string. The UUID's
dashes are illegal in an *unquoted* Postgres identifier, so every LISTEN raised
a syntax error (silently swallowed → the SSE stream fell back to polling). The
channel must be quoted; this pins that it round-trips as a valid quoted
identifier that still matches pg_notify's literal name.
"""

from __future__ import annotations

import uuid

from psycopg import sql

from app.services.learn_notify import learn_notify_channel


def test_channel_contains_dashes_so_it_must_be_quoted() -> None:
    ch = learn_notify_channel(uuid.uuid4())
    assert ch.startswith("zivo_learn_")
    assert "-" in ch  # the UUID's dashes are exactly why an f-string LISTEN failed


def test_channel_quotes_to_a_valid_identifier_matching_the_literal_name() -> None:
    ch = learn_notify_channel(uuid.uuid4())
    quoted = sql.Identifier(ch).as_string(None)
    # Double-quoted, and the exact channel name survives inside the quotes so it
    # still matches the name pg_notify() publishes on.
    assert quoted == f'"{ch}"'
