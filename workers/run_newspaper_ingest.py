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

from app.engine_runtime import choose, pick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("newspaper_ingest")

# Wide enough to see recent filename dates; hard cap avoids unbounded history walks.
_RECONCILE_SAFETY_MAX = 500
_RECONCILE_INTERVAL_SEC = 900
_RECONCILE_TIMEOUT_SEC = 600
_WATCHDOG_INTERVAL_SEC = 300


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _raise(exc: BaseException) -> None:
    raise exc


def _raise_from(cause: BaseException, wrapped: BaseException) -> None:
    raise wrapped from cause


def resolve_telegram_session(session_raw: str):
    """Return a Telethon session object or raise SystemExit on unsafe paths.

    Prefer StringSession (len>80 or starts with ``1``). Otherwise only a simple
    cwd session-file stem is allowed — no path separators / traversal.
    """
    from telethon.sessions import StringSession
    import re

    raw = (session_raw or "").strip()
    pick(not raw, lambda: _raise(SystemExit("TELEGRAM_SESSION required")), lambda: None)
    return pick(
        len(raw) > 80 or raw.startswith("1"),
        lambda: StringSession(raw),
        lambda: pick(
            bool(re.fullmatch(r"[A-Za-z0-9._-]+", raw)),
            lambda: raw,
            lambda: _raise(
                SystemExit(
                    "TELEGRAM_SESSION must be a Telethon StringSession or a simple session name "
                    "(letters/digits/._- only — no path separators)"
                )
            ),
        ),
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
        pick(
            key in existing,
            lambda: None,
            lambda k=key, mid=msg_id: pick(
                k not in winners,
                lambda: winners.__setitem__(k, int(mid)),
                lambda: None,
            ),
        )
    return sorted(winners.values())


def _message_filename(message) -> str:  # noqa: ANN001
    fname = [""]

    def _from_doc() -> None:
        for attr in message.document.attributes or []:
            fname[0] = getattr(attr, "file_name", None) or fname[0]
        pick(
            not fname[0] and bool(message.file),
            lambda: fname.__setitem__(0, getattr(message.file, "name", "") or ""),
            lambda: None,
        )

    pick(bool(message) and bool(getattr(message, "document", None)), _from_doc, lambda: None)
    return fname[0] or ""


def _scan_pdf_for_gap(db, message) -> tuple[int, str, date] | None:
    """Cheap (paper_slug, edition_date) for reconcile gap-fill — no LLM."""
    from app.repositories import newspaper as newspaper_repo
    from app.services.newspaper_naming import cheap_paper_slug_hint, resolve_edition_date

    def _after_pdf() -> tuple[int, str, date] | None:
        fname = _message_filename(message)
        caption = message.message or ""
        paper_slug = cheap_paper_slug_hint(db, filename=fname, caption=caption)

        def _dated() -> tuple[int, str, date] | None:
            edition_date = resolve_edition_date(
                filename=fname,
                caption=caption,
                message_date=message.date or datetime.now(timezone.utc),
            )
            return int(message.id), paper_slug, edition_date

        return pick(
            not paper_slug or not newspaper_repo.is_brand_allowed(db, paper_slug),
            lambda: None,
            _dated,
        )

    return pick(not _is_pdf_message(message), lambda: None, _after_pdf)


async def _noop() -> None:
    return None


async def _process_message(client, message, *, client_lock: asyncio.Lock) -> None:
    """Ingest one channel PDF. DB sessions are short — never held across await."""
    from io import BytesIO

    from app.db import SessionLocal
    from app.repositories import newspaper as newspaper_repo
    from app.services.newspaper import create_edition_from_pdf, ensure_brand_and_allowed
    from app.services.newspaper_naming import cheap_paper_slug_hint, parse_edition_meta_async

    async def _ingest() -> None:
        mime = (message.document.mime_type or "").lower()
        fname = _message_filename(message)

        async def _after_mime() -> None:
            caption = message.message or ""
            skip = [False]
            with SessionLocal() as db:
                hint = cheap_paper_slug_hint(db, filename=fname, caption=caption)
                pick(
                    bool(hint) and not newspaper_repo.is_brand_allowed(db, hint),
                    lambda: (
                        logger.info(
                            "skip paper %s (%s) — not on allowlist msg=%s",
                            hint,
                            fname,
                            message.id,
                        ),
                        skip.__setitem__(0, True),
                    ),
                    lambda: None,
                )
            await pick(skip[0], _noop, lambda: _after_hint(fname, caption))

        async def _after_hint(fname: str, caption: str) -> None:
            msg_date = message.date or datetime.now(timezone.utc)
            with SessionLocal() as db:
                parsed = await parse_edition_meta_async(
                    db, filename=fname or "edition.pdf", caption=caption, message_date=msg_date
                )
            skip = [False]
            with SessionLocal() as db:
                pick(
                    not ensure_brand_and_allowed(
                        db, paper_slug=parsed.paper_slug, paper_title=parsed.paper_title
                    ),
                    lambda: (
                        logger.info(
                            "skip paper %s (%s) — not on allowlist msg=%s",
                            parsed.paper_slug,
                            fname,
                            message.id,
                        ),
                        skip.__setitem__(0, True),
                    ),
                    lambda: None,
                )
                existing = newspaper_repo.get_edition_by_paper_day(
                    db, paper_slug=parsed.paper_slug, edition_date=parsed.edition_date
                )
                pick(
                    bool(existing) and not skip[0],
                    lambda: (
                        logger.info(
                            "skip duplicate %s %s (already %s) msg=%s",
                            parsed.paper_slug,
                            parsed.edition_date,
                            existing.get("status"),
                            message.id,
                        ),
                        skip.__setitem__(0, True),
                    ),
                    lambda: None,
                )
            await pick(skip[0], _noop, lambda: _download(fname, parsed))

        async def _download(fname: str, parsed) -> None:
            buf = BytesIO()
            async with client_lock:
                await client.download_media(message, file=buf)
            data = buf.getvalue()

            async def _store() -> None:
                with SessionLocal() as db:
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
                pick(
                    bool(edition_id),
                    lambda: logger.info(
                        "ingested %s %s via %s → %s",
                        parsed.paper_title,
                        parsed.edition_date,
                        parsed.source,
                        edition_id,
                    ),
                    lambda: None,
                )

            await pick(not data, lambda: logger.warning("empty download msg=%s", message.id) or _noop(), _store)

        await pick(
            "pdf" not in mime and not fname.lower().endswith(".pdf"),
            _noop,
            _after_mime,
        )

    await pick(not message or not getattr(message, "document", None), _noop, _ingest)


def _is_pdf_message(message) -> bool:  # noqa: ANN001
    def _check() -> bool:
        mime = (message.document.mime_type or "").lower()
        fname = _message_filename(message)
        return "pdf" in mime or fname.lower().endswith(".pdf")

    return pick(not message or not getattr(message, "document", None), lambda: False, _check)


async def _reconcile(
    client,
    entity,
    *,
    client_lock: asyncio.Lock,
    safety_max: int = _RECONCILE_SAFETY_MAX,
) -> None:
    """Gap-fill: cook Telegram PDFs whose (paper, edition_date) is missing in DB.

    Scan recent channel history (safety cap). Cheap filename hints drive
    plan_gap_fill so we only full-parse / download true holes.
    Oldest msg_id first → first city wins. sync_cursor = tip watermark only.
    """
    from app.db import SessionLocal
    from app.repositories import newspaper as newspaper_repo

    tip_id: int | None = None
    pdfs: list = []
    async with client_lock:
        async for message in client.iter_messages(entity, limit=safety_max):
            tip_id = choose(tip_id is None, int(message.id), tip_id)
            pick(_is_pdf_message(message), lambda m=message: pdfs.append(m), lambda: None)

    with SessionLocal() as db:
        existing = newspaper_repo.list_existing_paper_days(db)
    seen: list[tuple[int, str, date]] = []
    by_id: dict[int, object] = {}
    for message in pdfs:
        with SessionLocal() as db:
            scanned = _scan_pdf_for_gap(db, message)
        pick(
            scanned is None,
            lambda: None,
            lambda s=scanned, m=message: (
                seen.append((s[0], s[1], s[2])),
                by_id.__setitem__(s[0], m),
            ),
        )

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

        async def _cook(m=message, mid=msg_id) -> None:
            try:
                await _process_message(client, m, client_lock=client_lock)
            except Exception:
                logger.exception("reconcile failed msg=%s", mid)

        await pick(message is None, _noop, _cook)

    def _set_cursor() -> None:
        with SessionLocal() as db:
            newspaper_repo.set_sync_cursor(db, tip_id)

    pick(tip_id is not None, _set_cursor, lambda: None)


# Private-channel title substring used when channel_label exact match misses.
_FALLBACK_TITLE_SUBSTR = "MyBookZon ENGLISH"


def _entity_ref(raw: str):
    """Telethon wants int peer ids; string \"-100…\" fails with Cannot find entity."""
    ref = (raw or "").strip()
    return pick(
        not ref,
        lambda: "",
        lambda: pick(ref.lstrip("-").isdigit(), lambda: int(ref), lambda: ref),
    )


def _dialog_id_matches(dialog_id: int, entity_id: int | None, target) -> bool:
    """Match marked peer id (-100…) or bare Channel.id against configured ref."""

    def _bare_from_target() -> bool:
        try:
            bare = int(str(target)[4:])
        except ValueError:
            return False
        return entity_id is not None and int(entity_id) == bare

    def _bare_from_dialog() -> bool:
        try:
            return int(str(dialog_id)[4:]) == target
        except ValueError:
            return False

    return pick(
        not isinstance(target, int),
        lambda: False,
        lambda: pick(
            dialog_id == target,
            lambda: True,
            lambda: pick(
                entity_id is not None and int(entity_id) == target,
                lambda: True,
                lambda: pick(
                    target < 0 and str(target).startswith("-100"),
                    _bare_from_target,
                    lambda: pick(
                        target > 0 and dialog_id < 0 and str(dialog_id).startswith("-100"),
                        _bare_from_dialog,
                        lambda: False,
                    ),
                ),
            ),
        ),
    )


def _dialog_title_matches(dialog_name: str, *, label: str, ref: str) -> str | None:
    """Return match reason if dialog title matches label / ref / MyBookZon fallback."""
    name = (dialog_name or "").strip()
    lower = name.lower()
    return pick(
        not name,
        lambda: None,
        lambda: pick(
            bool(label) and name == label.strip(),
            lambda: "title_exact_label",
            lambda: pick(
                bool(ref) and name == ref.strip(),
                lambda: "title_exact_ref",
                lambda: pick(
                    _FALLBACK_TITLE_SUBSTR.lower() in lower,
                    lambda: "title_contains_mybookzon_english",
                    lambda: pick(
                        bool(label)
                        and "mybookzon" in label.lower()
                        and "mybookzon english" in lower,
                        lambda: "title_contains_label_hint",
                        lambda: None,
                    ),
                ),
            ),
        ),
    )


def _log_task_result(task: asyncio.Task) -> None:
    def _check_exc() -> None:
        exc = task.exception()
        pick(
            exc is not None,
            lambda: logger.error("background task %s failed", task.get_name(), exc_info=exc),
            lambda: None,
        )

    pick(task.cancelled(), lambda: None, _check_exc)


async def run() -> None:
    try:
        from telethon import TelegramClient, events
        from telethon.tl.types import Channel, InputPeerChannel
        from telethon.utils import get_peer_id
    except ImportError as exc:
        _raise_from(
            exc,
            SystemExit("telethon is required for newspaper ingest — pip install telethon"),
        )

    api_id = int(_env("TELEGRAM_API_ID") or "0")
    api_hash = _env("TELEGRAM_API_HASH")
    session_raw = _env("TELEGRAM_SESSION")
    pick(
        not api_id or not api_hash or not session_raw,
        lambda: _raise(SystemExit("TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION required")),
        lambda: None,
    )

    session = resolve_telegram_session(session_raw)

    from app.db import SessionLocal

    client = TelegramClient(session, api_id, api_hash)
    await client.start()
    logger.info("telegram client started")

    client_lock = asyncio.Lock()
    reconcile_lock = asyncio.Lock()
    ingest_lock = asyncio.Lock()

    # Cache channel settings + resolved entity — avoid DB/Telegram on every event.
    _cache: dict = {
        "entity": None,
        "ref": None,
        "label": None,
        "settings_loaded": False,
    }

    def _load_channel_settings() -> tuple[str, str]:
        with SessionLocal() as db:
            from app.repositories import newspaper as newspaper_repo

            s = newspaper_repo.get_settings(db)
            ref = (s.get("channel_ref") or "").strip()
            label = (s.get("channel_label") or "").strip()
        _cache["ref"] = ref
        _cache["label"] = label
        _cache["settings_loaded"] = True
        return ref, label

    def _channel_settings() -> tuple[str, str]:
        return pick(
            bool(_cache.get("settings_loaded")),
            lambda: ((_cache.get("ref") or ""), (_cache.get("label") or "")),
            _load_channel_settings,
        )

    def _maybe_persist_peer_id(ref: str, label: str, entity) -> None:
        """If dialogs revealed a stable marked peer id, store it without wiping cursor."""
        peer_id = [""]
        try:
            peer_id[0] = str(get_peer_id(entity))
        except Exception:
            return

        def _persist() -> None:
            with SessionLocal() as db:
                from app.repositories import newspaper as newspaper_repo

                newspaper_repo.update_channel_ref_keep_cursor(
                    db,
                    channel_ref=peer_id[0],
                    channel_label=label or None,
                )
            logger.info(
                "updated newspaper channel_ref %r → %r (keep sync_cursor)",
                ref,
                peer_id[0],
            )
            _cache["ref"] = peer_id[0]

        pick(
            not peer_id[0] or peer_id[0] == ref,
            lambda: None,
            lambda: pick(
                bool(ref) and _entity_ref(ref) == _entity_ref(peer_id[0]),
                lambda: None,
                _persist,
            ),
        )

    async def _entity_from_dialog(dialog, *, path: str):
        entity = dialog.entity
        title = (dialog.name or "").strip()

        async def _via_peer():
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

        async def _via_entity():
            logger.info(
                "resolved newspaper channel via %s (dialog.entity) id=%s title=%r",
                path,
                getattr(entity, "id", None),
                title,
            )
            return entity

        return await pick(
            isinstance(entity, Channel) and getattr(entity, "access_hash", None) is not None,
            _via_peer,
            _via_entity,
        )

    async def resolve_entity():
        ref, label = _channel_settings()

        async def _missing():
            logger.warning("newspaper channel_ref empty — set via admin /api/newspaper/admin/channel")
            return None

        async def _cached():
            return _cache["entity"]

        async def _resolve():
            target = pick(bool(ref), lambda: _entity_ref(ref), lambda: "")
            found = [None]

            async def _try_get_entity():
                try:
                    entity = await client.get_entity(target)
                    logger.info(
                        "resolved newspaper channel via get_entity ref=%r id=%s",
                        ref,
                        getattr(entity, "id", None),
                    )
                    _cache.update({"entity": entity, "ref": ref, "label": label})
                    found[0] = entity
                except Exception as exc:
                    logger.warning(
                        "get_entity(%r) failed (%s: %s) — falling back to dialogs",
                        target,
                        type(exc).__name__,
                        exc,
                    )

            async def _from_dialogs():
                async for d in client.iter_dialogs(limit=400):
                    async def _consider(dialog=d):
                        ent_id = getattr(dialog.entity, "id", None)
                        ent_id_int = pick(ent_id is not None, lambda: int(ent_id), lambda: None)

                        async def _from_id():
                            entity = await _entity_from_dialog(dialog, path="dialogs_id")
                            _cache.update({"entity": entity, "ref": ref, "label": label})
                            _maybe_persist_peer_id(ref, label, entity)
                            found[0] = entity

                        async def _from_title():
                            title_path = _dialog_title_matches(
                                dialog.name or "", label=label, ref=ref
                            )

                            async def _use():
                                entity = await _entity_from_dialog(
                                    dialog, path=f"dialogs_{title_path}"
                                )
                                _cache.update({"entity": entity, "ref": ref, "label": label})
                                _maybe_persist_peer_id(ref, label, entity)
                                found[0] = entity

                            await pick(bool(title_path), _use, _noop)

                        await pick(
                            _dialog_id_matches(int(dialog.id), ent_id_int, target),
                            _from_id,
                            _from_title,
                        )

                    await pick(found[0] is not None, _noop, _consider)

            await pick(target != "", _try_get_entity, _noop)
            await pick(found[0] is not None, _noop, _from_dialogs)
            pick(
                found[0] is not None,
                lambda: None,
                lambda: _raise(
                    ValueError(f"Cannot resolve newspaper channel ref={ref!r} label={label!r}")
                ),
            )
            return found[0]

        return await pick(
            not ref and not label,
            _missing,
            lambda: pick(
                _cache["entity"] is not None
                and _cache["ref"] == ref
                and _cache["label"] == label,
                _cached,
                _resolve,
            ),
        )

    # Live handler — PDFs from the newspaper channel only; no DB until we match.
    @client.on(events.NewMessage)
    async def on_new(event):  # noqa: ANN001
        async def _ingest_live() -> None:
            async with ingest_lock:
                try:
                    await _process_message(
                        client, event.message, client_lock=client_lock
                    )
                except Exception:
                    logger.exception("live ingest failed")

        async def _handle() -> None:
            ref, label = _channel_settings()

            async def _gated() -> None:
                cached_entity = _cache.get("entity")

                async def _with_cache() -> None:
                    chat = await event.get_chat()
                    await pick(
                        getattr(chat, "id", None) != getattr(cached_entity, "id", None),
                        _noop,
                        _ingest_live,
                    )

                async def _without_cache() -> None:
                    entity = [None]
                    try:
                        entity[0] = await resolve_entity()
                    except Exception:
                        logger.exception("resolve channel failed")
                        return

                    async def _after_entity() -> None:
                        chat = await event.get_chat()
                        await pick(
                            getattr(chat, "id", None) != getattr(entity[0], "id", None),
                            _noop,
                            _ingest_live,
                        )

                    await pick(entity[0] is None, _noop, _after_entity)

                await pick(cached_entity is not None, _with_cache, _without_cache)

            await pick(not ref and not label, _noop, _gated)

        await pick(not _is_pdf_message(event.message), _noop, _handle)

    async def _run_reconcile_once() -> None:
        async def _skip() -> None:
            logger.warning("reconcile already running — skip overlapping cycle")

        async def _run() -> None:
            async with reconcile_lock:
                entity = await resolve_entity()

                async def _do() -> None:
                    async with ingest_lock:
                        await asyncio.wait_for(
                            _reconcile(client, entity, client_lock=client_lock),
                            timeout=_RECONCILE_TIMEOUT_SEC,
                        )

                await pick(entity is None, _noop, _do)

        await pick(reconcile_lock.locked(), _skip, _run)

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
                async def _reconnect() -> None:
                    logger.warning("telegram client disconnected — reconnecting")
                    await client.connect()

                    async def _unauthorized() -> None:
                        logger.error("telegram session no longer authorized")

                    async def _ok() -> None:
                        logger.info("telegram client reconnected")
                        await _run_reconcile_once()

                    await pick(not await client.is_user_authorized(), _unauthorized, _ok)

                await pick(client.is_connected(), _noop, _reconnect)
            except Exception:
                logger.exception("connection watchdog error")

    _load_channel_settings()
    try:
        await resolve_entity()
    except Exception:
        logger.exception("initial channel resolve failed")

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


pick(__name__ == "__main__", main, lambda: None)
