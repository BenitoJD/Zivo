"""Telethon newspaper ingest — watch channel PDFs, cook once.

Reads channel_ref from qb.newspaper_settings (hot). Secrets via env:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION (session string or path).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("newspaper_ingest")

# Wide enough to see recent filename dates; hard cap avoids unbounded history walks.
_RECONCILE_SAFETY_MAX = 500
_RECONCILE_INTERVAL_SEC = 900
_RECONCILE_TIMEOUT_SEC = 600
_WATCHDOG_INTERVAL_SEC = 300


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def resolve_telegram_session(session_raw: str):
    """Return a Telethon session object or raise SystemExit on unsafe paths.

    Prefer StringSession (len>80 or starts with ``1``). Otherwise only a simple
    cwd session-file stem is allowed — no path separators / traversal.
    """
    from telethon.sessions import StringSession
    import re

    raw = (session_raw or "").strip()
    if not raw:
        raise SystemExit("TELEGRAM_SESSION required")
    if len(raw) > 80 or raw.startswith("1"):
        return StringSession(raw)
    if re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        return raw
    raise SystemExit(
        "TELEGRAM_SESSION must be a Telethon StringSession or a simple session name "
        "(letters/digits/._- only — no path separators)"
    )


def plan_gap_fill(
    seen: list[tuple[int, str, date]],
    existing: set[tuple[str, date]],
) -> list[int]:
    """Which telegram msg ids to cook.

    ``seen`` = (msg_id, paper_slug, edition_date) from channel PDFs.
    ``existing`` = (paper_slug, edition_date) already in DB (ignore purged).
    Missing pairs only; lowest msg_id wins (first city for that paper/day).
    """
    winners: dict[tuple[str, date], int] = {}
    for msg_id, paper_slug, edition_date in sorted(seen, key=lambda t: int(t[0])):
        key = (paper_slug, edition_date)
        if key in existing:
            continue
        if key not in winners:
            winners[key] = int(msg_id)
    return sorted(winners.values())


def _message_filename(message) -> str:  # noqa: ANN001
    fname = ""
    if message and message.document:
        for attr in message.document.attributes or []:
            fname = getattr(attr, "file_name", None) or fname
        if not fname and message.file:
            fname = getattr(message.file, "name", "") or ""
    return fname or ""


def _scan_pdf_for_gap(db, message) -> tuple[int, str, date] | None:
    """Cheap (paper_slug, edition_date) for reconcile gap-fill — no LLM."""
    from app.services.newspaper_naming import cheap_paper_slug_hint, resolve_edition_date

    if not _is_pdf_message(message):
        return None
    fname = _message_filename(message)
    caption = message.message or ""
    msg_date = message.date or datetime.now(timezone.utc)
    paper_slug = cheap_paper_slug_hint(db, filename=fname, caption=caption)
    if not paper_slug:
        return None
    edition_date = resolve_edition_date(
        filename=fname, caption=caption, message_date=msg_date
    )
    return int(message.id), paper_slug, edition_date


async def _process_message(client, db, message, *, client_lock: asyncio.Lock) -> None:
    from app.repositories import newspaper as newspaper_repo
    from app.services.newspaper import create_edition_from_pdf, ensure_brand_and_allowed
    from app.services.newspaper_naming import cheap_paper_slug_hint, parse_edition_meta_async

    if not message or not message.document:
        return
    mime = (message.document.mime_type or "").lower()
    fname = _message_filename(message)
    if "pdf" not in mime and not fname.lower().endswith(".pdf"):
        return

    caption = message.message or ""

    # Gate before LLM: skip known non-allowlisted brands cheaply.
    hint = cheap_paper_slug_hint(db, filename=fname, caption=caption)
    if hint and not newspaper_repo.is_brand_allowed(db, hint):
        logger.info(
            "skip paper %s (%s) — not on allowlist msg=%s",
            hint,
            fname,
            message.id,
        )
        return

    msg_date = message.date or datetime.now(timezone.utc)
    parsed = await parse_edition_meta_async(
        db, filename=fname or "edition.pdf", caption=caption, message_date=msg_date
    )

    # Gate before multi‑MB download: allowlist + one edition per paper/day.
    if not ensure_brand_and_allowed(
        db, paper_slug=parsed.paper_slug, paper_title=parsed.paper_title
    ):
        logger.info(
            "skip paper %s (%s) — not on allowlist msg=%s",
            parsed.paper_slug,
            fname,
            message.id,
        )
        return
    existing = newspaper_repo.get_edition_by_paper_day(
        db, paper_slug=parsed.paper_slug, edition_date=parsed.edition_date
    )
    if existing:
        logger.info(
            "skip duplicate %s %s (already %s) msg=%s",
            parsed.paper_slug,
            parsed.edition_date,
            existing.get("status"),
            message.id,
        )
        return

    from io import BytesIO

    buf = BytesIO()
    async with client_lock:
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
    fname = _message_filename(message)
    return "pdf" in mime or fname.lower().endswith(".pdf")


async def _reconcile(
    client,
    entity,
    db,
    *,
    client_lock: asyncio.Lock,
    safety_max: int = _RECONCILE_SAFETY_MAX,
) -> None:
    """Gap-fill: cook Telegram PDFs whose (paper, edition_date) is missing in DB.

    Scan recent channel history (safety cap). Cheap filename hints drive
    plan_gap_fill so we only full-parse / download true holes.
    Oldest msg_id first → first city wins. sync_cursor = tip watermark only.
    """
    from app.repositories import newspaper as newspaper_repo

    tip_id: int | None = None
    pdfs: list = []
    async with client_lock:
        async for message in client.iter_messages(entity, limit=safety_max):
            if tip_id is None:
                tip_id = int(message.id)
            if _is_pdf_message(message):
                pdfs.append(message)

    existing = newspaper_repo.list_existing_paper_days(db)
    seen: list[tuple[int, str, date]] = []
    by_id: dict[int, object] = {}
    for message in pdfs:
        scanned = _scan_pdf_for_gap(db, message)
        if scanned is None:
            continue
        msg_id, paper_slug, edition_date = scanned
        seen.append((msg_id, paper_slug, edition_date))
        by_id[msg_id] = message

    winners = plan_gap_fill(seen, existing)
    logger.info(
        "reconcile gap-fill pdfs=%s candidates=%s cook=%s tip=%s",
        len(pdfs),
        len(seen),
        len(winners),
        tip_id,
    )
    for msg_id in winners:
        message = by_id.get(msg_id)
        if message is None:
            continue
        try:
            await _process_message(client, db, message, client_lock=client_lock)
        except Exception:
            logger.exception("reconcile failed msg=%s", msg_id)
    if tip_id is not None:
        newspaper_repo.set_sync_cursor(db, tip_id)


# Private-channel title substring used when channel_label exact match misses.
_FALLBACK_TITLE_SUBSTR = "MyBookZon ENGLISH"


def _entity_ref(raw: str):
    """Telethon wants int peer ids; string \"-100…\" fails with Cannot find entity."""
    ref = (raw or "").strip()
    if not ref:
        return ""
    if ref.lstrip("-").isdigit():
        return int(ref)
    return ref


def _dialog_id_matches(dialog_id: int, entity_id: int | None, target) -> bool:
    """Match marked peer id (-100…) or bare Channel.id against configured ref."""
    if not isinstance(target, int):
        return False
    if dialog_id == target:
        return True
    if entity_id is not None and int(entity_id) == target:
        return True
    # Bare channel id vs marked -100XXXXXXXXX peer id.
    if target < 0 and str(target).startswith("-100"):
        try:
            bare = int(str(target)[4:])
        except ValueError:
            return False
        return entity_id is not None and int(entity_id) == bare
    if target > 0 and dialog_id < 0 and str(dialog_id).startswith("-100"):
        try:
            return int(str(dialog_id)[4:]) == target
        except ValueError:
            return False
    return False


def _dialog_title_matches(dialog_name: str, *, label: str, ref: str) -> str | None:
    """Return match reason if dialog title matches label / ref / MyBookZon fallback."""
    name = (dialog_name or "").strip()
    if not name:
        return None
    if label and name == label.strip():
        return "title_exact_label"
    if ref and name == ref.strip():
        return "title_exact_ref"
    lower = name.lower()
    if _FALLBACK_TITLE_SUBSTR.lower() in lower:
        return "title_contains_mybookzon_english"
    if label and "mybookzon" in label.lower() and "mybookzon english" in lower:
        return "title_contains_label_hint"
    return None


def _log_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background task %s failed", task.get_name(), exc_info=exc)


async def run() -> None:
    try:
        from telethon import TelegramClient, events
        from telethon.tl.types import Channel, InputPeerChannel
        from telethon.utils import get_peer_id
    except ImportError as exc:
        raise SystemExit(
            "telethon is required for newspaper ingest — pip install telethon"
        ) from exc

    api_id = int(_env("TELEGRAM_API_ID") or "0")
    api_hash = _env("TELEGRAM_API_HASH")
    session_raw = _env("TELEGRAM_SESSION")
    if not api_id or not api_hash or not session_raw:
        raise SystemExit("TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION required")

    session = resolve_telegram_session(session_raw)

    from app.db import SessionLocal

    client = TelegramClient(session, api_id, api_hash)
    await client.start()
    logger.info("telegram client started")

    client_lock = asyncio.Lock()
    reconcile_lock = asyncio.Lock()

    # Cache resolved entity — avoid re-iterating dialogs (flood) on every event.
    _cache: dict = {"entity": None, "ref": None, "label": None}

    def _channel_settings() -> tuple[str, str]:
        with SessionLocal() as db:
            from app.repositories import newspaper as newspaper_repo

            s = newspaper_repo.get_settings(db)
            return (
                (s.get("channel_ref") or "").strip(),
                (s.get("channel_label") or "").strip(),
            )

    def _maybe_persist_peer_id(ref: str, label: str, entity) -> None:
        """If dialogs revealed a stable marked peer id, store it without wiping cursor."""
        try:
            peer_id = str(get_peer_id(entity))
        except Exception:
            return
        if not peer_id or peer_id == ref:
            return
        # Only rewrite when current ref missing/non-numeric or differs from discovered id.
        if ref and _entity_ref(ref) == _entity_ref(peer_id):
            return
        with SessionLocal() as db:
            from app.repositories import newspaper as newspaper_repo

            newspaper_repo.update_channel_ref_keep_cursor(
                db,
                channel_ref=peer_id,
                channel_label=label or None,
            )
        logger.info(
            "updated newspaper channel_ref %r → %r (keep sync_cursor)",
            ref,
            peer_id,
        )
        _cache["ref"] = peer_id

    async def _entity_from_dialog(dialog, *, path: str):
        entity = dialog.entity
        title = (dialog.name or "").strip()
        # Prefer InputPeerChannel(access_hash) so Telethon can address private channels.
        if isinstance(entity, Channel) and getattr(entity, "access_hash", None) is not None:
            peer = InputPeerChannel(
                channel_id=int(entity.id),
                access_hash=int(entity.access_hash),
            )
            resolved = await client.get_entity(peer)
            logger.info(
                "resolved newspaper channel via %s (InputPeerChannel) id=%s title=%r",
                path,
                get_peer_id(resolved),
                title,
            )
            return resolved
        logger.info(
            "resolved newspaper channel via %s (dialog.entity) id=%s title=%r",
            path,
            getattr(entity, "id", None),
            title,
        )
        return entity

    async def resolve_entity():
        ref, label = _channel_settings()
        if not ref and not label:
            logger.warning("newspaper channel_ref empty — set via admin /api/newspaper/admin/channel")
            return None
        if (
            _cache["entity"] is not None
            and _cache["ref"] == ref
            and _cache["label"] == label
        ):
            return _cache["entity"]

        target = _entity_ref(ref) if ref else ""
        if target != "":
            try:
                entity = await client.get_entity(target)
                logger.info(
                    "resolved newspaper channel via get_entity ref=%r id=%s",
                    ref,
                    getattr(entity, "id", None),
                )
                _cache.update({"entity": entity, "ref": ref, "label": label})
                return entity
            except Exception as exc:
                logger.warning(
                    "get_entity(%r) failed (%s: %s) — falling back to dialogs",
                    target,
                    type(exc).__name__,
                    exc,
                )

        # Warm entity cache from dialogs (needed after fresh StringSession on a new host).
        async for d in client.iter_dialogs(limit=400):
            ent_id = getattr(d.entity, "id", None)
            if _dialog_id_matches(int(d.id), int(ent_id) if ent_id is not None else None, target):
                entity = await _entity_from_dialog(d, path="dialogs_id")
                _cache.update({"entity": entity, "ref": ref, "label": label})
                _maybe_persist_peer_id(ref, label, entity)
                return entity
            title_path = _dialog_title_matches(d.name or "", label=label, ref=ref)
            if title_path:
                entity = await _entity_from_dialog(d, path=f"dialogs_{title_path}")
                _cache.update({"entity": entity, "ref": ref, "label": label})
                _maybe_persist_peer_id(ref, label, entity)
                return entity

        raise ValueError(f"Cannot resolve newspaper channel ref={ref!r} label={label!r}")

    # Live handler — filter in-process against current channel id.
    @client.on(events.NewMessage)
    async def on_new(event):  # noqa: ANN001
        ref, _label = _channel_settings()
        if not ref and not _label:
            return
        try:
            entity = await resolve_entity()
        except Exception:
            logger.exception("resolve channel failed")
            return
        if entity is None:
            return
        chat = await event.get_chat()
        if getattr(chat, "id", None) != getattr(entity, "id", None):
            return
        with SessionLocal() as db:
            try:
                await _process_message(
                    client, db, event.message, client_lock=client_lock
                )
            except Exception:
                logger.exception("live ingest failed")

    async def _run_reconcile_once() -> None:
        if reconcile_lock.locked():
            logger.warning("reconcile already running — skip overlapping cycle")
            return
        async with reconcile_lock:
            entity = await resolve_entity()
            if entity is None:
                return
            with SessionLocal() as db:
                await asyncio.wait_for(
                    _reconcile(client, entity, db, client_lock=client_lock),
                    timeout=_RECONCILE_TIMEOUT_SEC,
                )

    # Startup + periodic reconcile
    async def reconcile_loop() -> None:
        while True:
            try:
                await _run_reconcile_once()
            except TimeoutError:
                logger.error(
                    "reconcile timed out after %ss — will retry next cycle",
                    _RECONCILE_TIMEOUT_SEC,
                )
            except Exception:
                logger.exception("reconcile loop error")
            await asyncio.sleep(_RECONCILE_INTERVAL_SEC)

    async def connection_watchdog() -> None:
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL_SEC)
            try:
                if client.is_connected():
                    continue
                logger.warning("telegram client disconnected — reconnecting")
                await client.connect()
                if not await client.is_user_authorized():
                    logger.error("telegram session no longer authorized")
                    continue
                logger.info("telegram client reconnected")
                await _run_reconcile_once()
            except Exception:
                logger.exception("connection watchdog error")

    for coro, name in (
        (reconcile_loop(), "newspaper-reconcile"),
        (connection_watchdog(), "newspaper-watchdog"),
    ):
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(_log_task_result)

    logger.info("newspaper ingest running")
    await client.run_until_disconnected()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
