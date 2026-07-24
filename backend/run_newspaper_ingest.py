"""Telethon newspaper ingest — watch channel PDFs, cook once.

Reads channel_ref from qb.newspaper_settings (hot). Secrets via env:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION (session string or path).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("newspaper_ingest")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


async def _process_message(client, db, message) -> None:
    from app.services.newspaper import create_edition_from_pdf
    from app.services.newspaper_naming import parse_edition_meta_async
    from app.repositories import newspaper as newspaper_repo

    if not message or not message.document:
        return
    mime = (message.document.mime_type or "").lower()
    fname = ""
    for attr in message.document.attributes or []:
        fname = getattr(attr, "file_name", None) or fname
    if not fname and message.file:
        fname = getattr(message.file, "name", "") or ""
    if "pdf" not in mime and not (fname or "").lower().endswith(".pdf"):
        return

    caption = message.message or ""
    msg_date = message.date or datetime.now(timezone.utc)
    parsed = await parse_edition_meta_async(
        db, filename=fname or "edition.pdf", caption=caption, message_date=msg_date
    )

    from io import BytesIO

    buf = BytesIO()
    await client.download_media(message, file=buf)
    data = buf.getvalue()
    if not data:
        logger.warning("empty download msg=%s", message.id)
        return

    edition_id = create_edition_from_pdf(
        db,
        filename=fname or f"{parsed.paper_slug}.pdf",
        data=data,
        paper_slug=parsed.paper_slug,
        paper_title=parsed.paper_title,
        edition_date=parsed.edition_date,
        telegram_msg_id=int(message.id),
        location_raw=parsed.location_raw,
    )
    newspaper_repo.set_sync_cursor(db, int(message.id))
    if edition_id:
        logger.info(
            "ingested %s %s via %s → %s",
            parsed.paper_title,
            parsed.edition_date,
            parsed.source,
            edition_id,
        )


def _is_pdf_message(message) -> bool:  # noqa: ANN001
    if not message or not message.document:
        return False
    mime = (message.document.mime_type or "").lower()
    fname = ""
    for attr in message.document.attributes or []:
        fname = getattr(attr, "file_name", None) or fname
    if not fname and message.file:
        fname = getattr(message.file, "name", "") or ""
    return "pdf" in mime or (fname or "").lower().endswith(".pdf")


async def _reconcile(client, entity, db, *, limit: int = 80) -> None:
    """Catch-up: collect PDFs above cursor, process oldest→newest so first city wins."""
    from app.repositories import newspaper as newspaper_repo

    settings = newspaper_repo.get_settings(db)
    cursor = settings.get("sync_cursor")
    candidates: list = []
    async for message in client.iter_messages(entity, limit=limit):
        if cursor is not None and message.id <= int(cursor):
            break
        if _is_pdf_message(message):
            candidates.append(message)

    # iter_messages is newest-first; reverse so earliest msg_id claims (paper, day).
    candidates.sort(key=lambda m: int(m.id))
    max_id: int | None = None
    for message in candidates:
        try:
            await _process_message(client, db, message)
            max_id = int(message.id) if max_id is None else max(max_id, int(message.id))
        except Exception:
            logger.exception("reconcile failed msg=%s", getattr(message, "id", None))
    if max_id is not None:
        newspaper_repo.set_sync_cursor(db, max_id)


async def run() -> None:
    try:
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise SystemExit(
            "telethon is required for newspaper ingest — pip install telethon"
        ) from exc

    api_id = int(_env("TELEGRAM_API_ID") or "0")
    api_hash = _env("TELEGRAM_API_HASH")
    session_raw = _env("TELEGRAM_SESSION")
    if not api_id or not api_hash or not session_raw:
        raise SystemExit("TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION required")

    # Session string preferred; otherwise treat as session file path stem.
    if len(session_raw) > 80 or session_raw.startswith("1"):
        session = StringSession(session_raw)
    else:
        session = session_raw

    from app.db import SessionLocal

    client = TelegramClient(session, api_id, api_hash)
    await client.start()
    logger.info("telegram client started")

    def _channel_ref() -> str:
        with SessionLocal() as db:
            from app.repositories import newspaper as newspaper_repo

            return (newspaper_repo.get_settings(db).get("channel_ref") or "").strip()

    async def resolve_entity():
        ref = _channel_ref()
        if not ref:
            logger.warning("newspaper channel_ref empty — set via admin /api/newspaper/admin/channel")
            return None
        return await client.get_entity(ref)

    # Live handler — filter in-process against current channel id.
    @client.on(events.NewMessage)
    async def on_new(event):  # noqa: ANN001
        ref = _channel_ref()
        if not ref:
            return
        try:
            entity = await client.get_entity(ref)
        except Exception:
            logger.exception("resolve channel failed")
            return
        chat = await event.get_chat()
        if getattr(chat, "id", None) != getattr(entity, "id", None):
            return
        with SessionLocal() as db:
            try:
                await _process_message(client, db, event.message)
            except Exception:
                logger.exception("live ingest failed")

    # Startup + periodic reconcile
    async def reconcile_loop() -> None:
        while True:
            try:
                entity = await resolve_entity()
                if entity is not None:
                    with SessionLocal() as db:
                        await _reconcile(client, entity, db)
            except Exception:
                logger.exception("reconcile loop error")
            await asyncio.sleep(900)

    asyncio.create_task(reconcile_loop())
    logger.info("newspaper ingest running")
    await client.run_until_disconnected()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
