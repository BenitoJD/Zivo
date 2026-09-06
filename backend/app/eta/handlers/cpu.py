"""CPU workload handlers."""

import logging
from uuid import UUID

from app.db import SessionLocal
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.eta.registry import eta
from app.models import Document, JobPriority, JobWorkload
from app.services.chunking import chunk_pages
from app.services.chunks import finalize_image_document, persist_document_index, upsert_page_chunks
from app.services.embed import embed_texts
from app.services.jobs import batch_enqueue_jobs, enqueue_job
from app.services.parse import document_bytes_hash, parse_document, parse_document_page
from app.services.rag_window import (
    chat_rag_window,
    refresh_rag_window_status,
    save_rag_window,
    sync_rag_window,
)
from app.services.question_pool import get_progress, selected_page_list
from app.services.storage import delete_object, fetch_object, get_json, ingest_tmp_key, put_json


logger = logging.getLogger(__name__)

_DOC_KIND_RULES = (
    Rule(when=(Pred("missing", "truthy"),), action="skip"),
    Rule(when=(Pred("image", "truthy"),), action="image"),
    Rule(when=(), action="parse"),
)

_RANGE_RULES = (
    Rule(when=(Pred("no_selected", "truthy"),), action="keep"),
    Rule(when=(Pred("has_pages", "truthy"),), action="by_list"),
    Rule(when=(), action="by_range"),
)


def _doc_kind(doc) -> str:
    hit = first_match(
        _DOC_KIND_RULES,
        {
            "missing": doc is None,
            "image": bool(doc) and str(doc.content_type).startswith("image/"),
        },
    )
    return hit.action


def _filter_selected_pages(pages: list, selected) -> list:
    page_list = (selected or {}).get("pages")
    hit = first_match(
        _RANGE_RULES,
        {
            "no_selected": not selected,
            "has_pages": isinstance(page_list, list) and bool(page_list),
        },
    )

    def _by_list() -> list:
        allowed = {int(p) for p in page_list}
        return list(filter(lambda p: int(p.get("page", 0)) in allowed, pages))

    def _by_range() -> list:
        page_from = int(selected.get("from", 1))
        page_to = int(selected.get("to", page_from))
        return list(filter(lambda p: page_from <= int(p.get("page", 0)) <= page_to, pages))

    return apply(hit.action, {"keep": lambda: pages, "by_list": _by_list, "by_range": _by_range})


def _set_progress(doc, db, value: int) -> None:
    def _write() -> None:
        doc.index_progress = value
        db.commit()

    pick(bool(doc), _write, lambda: None)


def _embed_passages(chunks: list) -> list:
    texts = [f"passage: {c['text']}" for c in chunks]
    return pick(bool(texts), lambda: embed_texts(texts), lambda: [])


def _finalize_image(db, document_id: UUID) -> dict:
    finalize_image_document(db, document_id)
    return {"document_id": str(document_id), "image": True}


@eta(name="ingest.parse_document", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def parse_document_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)

        def _parse() -> dict:
            raw = fetch_object(doc.storage_key)
            pages = parse_document(doc.content_type, raw)
            total_pages = len(pages)
            pages = _filter_selected_pages(pages, (doc.meta or {}).get("selected_range"))
            meta = dict(doc.meta or {})
            meta["page_count"] = total_pages or 1
            doc.meta = meta
            put_json(ingest_tmp_key(document_id, "pages"), {"pages": pages})
            doc.index_progress = 30
            db.commit()
            enqueue_job(
                db,
                name="ingest.chunk_pages",
                workload=JobWorkload.cpu,
                payload={"document_id": str(document_id)},
            )
            return {"document_id": str(document_id), "pages": len(pages)}

        return apply(
            _doc_kind(doc),
            {
                "skip": lambda: {"skipped": True},
                "image": lambda: _finalize_image(db, document_id),
                "parse": _parse,
            },
        )


@eta(name="ingest.chunk_pages", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def chunk_pages_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    pages_data = get_json(ingest_tmp_key(document_id, "pages"))
    pages = pages_data.get("pages", [])
    chunks = chunk_pages(pages)
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        _set_progress(doc, db, 50)
    put_json(ingest_tmp_key(document_id, "chunks"), {"chunks": chunks})
    with SessionLocal() as db:
        enqueue_job(
            db,
            name="ingest.embed_chunks",
            workload=JobWorkload.cpu,
            payload={"document_id": str(document_id)},
        )
    return {"document_id": str(document_id), "chunks": len(chunks)}


@eta(name="ingest.embed_chunks", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def embed_chunks_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    chunks_data = get_json(ingest_tmp_key(document_id, "chunks"))
    chunks = chunks_data.get("chunks", [])
    vectors = _embed_passages(chunks)

    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        _set_progress(doc, db, 80)
        count = persist_document_index(db, document_id, chunks, vectors)
        from app.services.question_generation import enqueue_generate_if_needed

        enqueue_generate_if_needed(db, document_id)

    def _cleanup(suffix: str) -> None:
        try:
            delete_object(ingest_tmp_key(document_id, suffix))
        except Exception:
            logger.debug("ingest temp-object cleanup failed", exc_info=True)

    for suffix in ("pages", "chunks"):
        _cleanup(suffix)

    return {"document_id": str(document_id), "chunks": count}


@eta(name="ingest.document", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def ingest_document_job(payload: dict) -> dict:
    """Bundled ingest: parse → chunk → embed → persist in ONE job.

    The happy path used to fan out across three separate CPU jobs
    (parse_document → chunk_pages → embed_chunks), each costing a worker poll
    cycle before the next could start. On a single-worker VPS that's two extra
    queue hops (up to ~2 × POLL_INTERVAL each) before generation can begin.
    Bundling collapses them to one hop while keeping the granular handlers
    registered for the retry/legacy paths and existing tests.
    """
    document_id = UUID(payload["document_id"])
    early = None
    parsed_pages = None

    def _finish(pages: list) -> dict:
        chunks = chunk_pages(pages)
        with SessionLocal() as db:
            doc = db.get(Document, document_id)
            _set_progress(doc, db, 60)
        vectors = _embed_passages(chunks)
        with SessionLocal() as db:
            doc = db.get(Document, document_id)
            _set_progress(doc, db, 90)
            count = persist_document_index(db, document_id, chunks, vectors)
            from app.services.question_generation import enqueue_generate_if_needed

            enqueue_generate_if_needed(db, document_id)
        return {"document_id": str(document_id), "chunks": count}

    with SessionLocal() as db:
        doc = db.get(Document, document_id)

        def _skip() -> None:
            nonlocal early
            early = {"skipped": True}

        def _image() -> None:
            nonlocal early
            early = _finalize_image(db, document_id)

        def _parse() -> None:
            nonlocal parsed_pages
            raw = fetch_object(doc.storage_key)
            pages = parse_document(doc.content_type, raw)
            total_pages = len(pages)
            pages = _filter_selected_pages(pages, (doc.meta or {}).get("selected_range"))
            meta = dict(doc.meta or {})
            meta["page_count"] = total_pages or 1
            doc.meta = meta
            doc.index_progress = 40
            db.commit()
            parsed_pages = pages

        apply(
            _doc_kind(doc),
            {"skip": _skip, "image": _image, "parse": _parse},
        )

    return pick(early is not None, lambda: early, lambda: _finish(parsed_pages))


@eta(name="ingest.page", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def ingest_page_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    page_number = int(payload["page_number"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)

        def _ingest() -> dict:
            raw = fetch_object(doc.storage_key)
            cache_key = ingest_tmp_key(document_id, "parsed_pages")
            content_hash = document_bytes_hash(raw)
            cached_pages: list[dict] | None = None

            def _from_cache() -> list[dict] | None:
                cached = get_json(cache_key)
                hit = cached.get("content_hash") == content_hash and isinstance(
                    cached.get("pages"), list
                )
                return pick(hit, lambda: cached["pages"], lambda: None)

            try:
                cached_pages = _from_cache()
            except Exception:
                cached_pages = None
            is_pdf = doc.content_type.startswith("application/pdf") or raw[:4] == b"%PDF"

            def _parse_all() -> list[dict]:
                pages = parse_document(doc.content_type, raw)
                put_json(cache_key, {"content_hash": content_hash, "pages": pages})
                return pages

            cached_pages = pick(
                cached_pages is None and not is_pdf,
                _parse_all,
                lambda: cached_pages,
            )
            page = parse_document_page(
                doc.content_type,
                raw,
                page_number,
                cached_pages=cached_pages,
            )
            from app.config import get_settings
            from app.services.content_worthiness import is_sparse_page_text

            def _maybe_ocr() -> None:
                from app.services.vision_ocr import transcribe_page_with_vision

                ocr_text = transcribe_page_with_vision(db, document_id, page_number)
                pick(bool(ocr_text), lambda: page.__setitem__("text", ocr_text), lambda: None)

            should_ocr = (
                get_settings().vision_ocr_enabled
                and is_sparse_page_text(page.get("text"))
                and is_pdf
            )
            pick(should_ocr, _maybe_ocr, lambda: None)
            chunks = chunk_pages([page])
            texts = [
                f"passage: {c['text']}"
                for c in filter(lambda c: c.get("text"), chunks)
            ]
            vectors = pick(bool(texts), lambda: embed_texts(texts), lambda: [])
            pick(
                bool(chunks) and bool(vectors),
                lambda: upsert_page_chunks(db, document_id, page_number, chunks, vectors),
                lambda: None,
            )
            from app.services.rag_window import mark_page_ingested

            mark_page_ingested(db, doc, page_number)
            db.commit()
            from app.services.background_prep import is_background_prep, on_page_ingested_for_prep

            def _ready_path() -> None:
                refresh_rag_window_status(db, document_id)
                from app.services.question_pool import maybe_enqueue_early_page_triage

                maybe_enqueue_early_page_triage(db, document_id, page_number=page_number)

            pick(
                is_background_prep(doc),
                lambda: on_page_ingested_for_prep(db, document_id),
                _ready_path,
            )
            return {
                "document_id": str(document_id),
                "page_number": page_number,
                "chunks": len(chunks),
            }

        return pick(not doc, lambda: {"skipped": True}, _ingest)


@eta(name="ingest.rag_window", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def ingest_rag_window_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)

        def _run() -> dict:
            progress = get_progress(doc)
            current = int(payload.get("current_page") or progress.get("current_page") or 1)
            study = selected_page_list(doc)
            target = chat_rag_window(current, study)
            save_rag_window(db, doc, target)
            pick(
                doc.status != "ready",
                lambda: setattr(doc, "status", "indexing"),
                lambda: None,
            )
            db.commit()
            pages_to_ingest = sync_rag_window(db, document_id, target)
            db.commit()
            pick(
                bool(pages_to_ingest),
                lambda: batch_enqueue_jobs(
                    db,
                    [
                        {
                            "name": "ingest.page",
                            "workload": JobWorkload.cpu,
                            "payload": {
                                "document_id": str(document_id),
                                "page_number": page,
                            },
                        }
                        for page in pages_to_ingest
                    ],
                    chunk_size=50,
                ),
                lambda: refresh_rag_window_status(db, document_id),
            )
            return {
                "document_id": str(document_id),
                "pages": target,
                "ingest_queued": pages_to_ingest,
            }

        return pick(not doc, lambda: {"skipped": True}, _run)


@eta(name="ingest.full_range", workload=JobWorkload.cpu, priority=JobPriority.LOW)
def ingest_full_range_job(payload: dict) -> dict:
    from app.services.background_prep import is_background_prep, start_full_range_ingest

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)

        def _run() -> dict:
            def _skip_mode() -> dict:
                return {"skipped": True, "reason": "not_background_prep"}

            def _ingest() -> dict:
                progress = get_progress(doc)
                current = int(payload.get("current_page") or progress.get("current_page") or 1)
                missing = start_full_range_ingest(db, doc, current_page=current)
                return {
                    "document_id": str(document_id),
                    "ingest_queued": missing,
                }

            return pick(not is_background_prep(doc), _skip_mode, _ingest)

        return pick(not doc, lambda: {"skipped": True}, _run)


@eta(name="learn.transition_prep", workload=JobWorkload.cpu)
def transition_prep_job(payload: dict) -> dict:
    from app.services.question_pool import (
        FIRST_QUESTION_BATCH_SIZE,
        count_assertions_on_page,
        effective_question_budget,
        enqueue_page_batch,
        enqueue_page_triage,
        get_page_coverage,
        get_progress,
        get_question_budget,
        mark_transition_prep_done,
        selected_page_list,
    )
    from app.services.question_budget import evaluate_generation_stop
    from app.services.session_design import (
        evaluate_serve_schedule,
        plan_first_cook_batch,
        plan_refill_batch,
        plan_transition_next,
    )

    document_id = UUID(payload["document_id"])
    current_page = int(payload["current_page"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)

        def _run() -> dict:
            progress = get_progress(doc)
            budget = effective_question_budget(doc, current_page, progress)
            generated = count_assertions_on_page(db, document_id, current_page)

            def _refill() -> None:
                remaining = budget - generated
                enqueue_page_batch(
                    db,
                    doc,
                    page=current_page,
                    batch_size=plan_refill_batch(remaining=remaining),
                    start_sequence=generated,
                )

            pick(
                not evaluate_generation_stop(
                    generated=generated, budget=budget, coverage_complete=False
                ),
                _refill,
                lambda: None,
            )

            study = selected_page_list(doc)

            def _next_from_study() -> int | None:
                idx = study.index(current_page)
                return pick(idx < len(study) - 1, lambda: study[idx + 1], lambda: None)

            next_page = pick(current_page in study, _next_from_study, lambda: None)
            answered_on_page = int(progress.get("answered_on_page") or 0)
            triage_budget = get_question_budget(doc, current_page)
            generate_next = evaluate_serve_schedule(
                answered_on_page=answered_on_page,
                page_budget=triage_budget,
            ).generate_next_page
            next_generated = pick(
                next_page is not None,
                lambda: count_assertions_on_page(db, document_id, next_page),
                lambda: 0,
            )
            next_has_coverage = pick(
                next_page is not None,
                lambda: bool(get_page_coverage(doc, next_page)),
                lambda: False,
            )
            next_plan = plan_transition_next(
                has_next=next_page is not None,
                next_has_coverage=next_has_coverage,
                next_generated=next_generated,
                generate_next_page=generate_next,
                current_budget=triage_budget,
            )

            def _prep_next() -> None:
                pick(next_plan.triage_next, lambda: enqueue_page_triage(db, doc, page=next_page), lambda: None)

                def _cook_next() -> None:
                    next_budget = effective_question_budget(doc, next_page, progress)
                    enqueue_page_batch(
                        db,
                        doc,
                        page=next_page,
                        batch_size=plan_first_cook_batch(
                            budget=next_budget, first_batch=FIRST_QUESTION_BATCH_SIZE
                        ),
                        start_sequence=0,
                    )

                pick(next_plan.cook_next, _cook_next, lambda: None)
                enqueue_job(
                    db,
                    name="ingest.rag_window",
                    workload=JobWorkload.cpu,
                    payload={"document_id": str(document_id), "current_page": next_page},
                )

            pick(next_page is not None, _prep_next, lambda: None)
            mark_transition_prep_done(db, doc, current_page)
            db.commit()
            return {
                "document_id": str(document_id),
                "current_page": current_page,
                "next_page": next_page,
            }

        return pick(not doc, lambda: {"skipped": True}, _run)


@eta(name="generate.questions", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def generate_questions_job(payload: dict) -> dict:
    from app.config import get_settings
    if not get_settings().mcq_generation_enabled:
        return {"status": "skipped", "reason": "mcq_generation_enabled=false"}
    from app.graphs.generation_graph import run_generation
    from app.services.question_pool import on_batch_failed

    document_id = UUID(payload["document_id"])
    page_number = int(payload.get("page_number") or 0)
    with SessionLocal() as db:
        try:
            return run_generation(db, document_id, payload)
        except Exception:
            on_batch_failed(db, document_id, page=page_number)
            raise


@eta(name="generate.coding", workload=JobWorkload.cpu)
def generate_coding_job(payload: dict) -> dict:
    """Generate LeetCode-style coding problems for one programmable page.

    Spawned by ``_maybe_spawn_coding`` when page triage flags a page as
    ``programmable``. Each problem is generate→verify→persist: the LLM's own
    reference solution must pass every hidden test in the sandbox, or the problem
    is discarded and retried (then dropped). Silence beats a broken problem.
    """
    from app.services.coding_generation import generate_coding_for_page
    from app.services.retrieval import fetch_chunks_for_page_range

    document_id = UUID(payload["document_id"])
    page_number = int(payload.get("page_number") or 0)
    from app.services.question_budget import plan_coding_page_yield

    count = plan_coding_page_yield(payload.get("count"))
    with SessionLocal() as db:
        chunks = fetch_chunks_for_page_range(
            db,
            document_ids=[document_id],
            page_start=page_number,
            page_end=page_number,
        )
        page_text = "\n\n".join(
            c.get("text", "") for c in filter(lambda c: c.get("text"), chunks)
        ).strip()

        def _empty() -> dict:
            return {"document_id": str(document_id), "page_number": page_number, "saved": 0}

        def _generate() -> dict:
            saved = generate_coding_for_page(
                db, document_id, page_number=page_number, page_text=page_text, count=count
            )
            return {
                "document_id": str(document_id),
                "page_number": page_number,
                "saved": len(saved),
                "problems": saved,
            }

        return pick(not page_text, _empty, _generate)


@eta(name="generate.debug", workload=JobWorkload.cpu)
def generate_debug_job(payload: dict) -> dict:
    """Cook debug diagnostic scenarios from a cook job or document page."""
    from app.services.debug_generation import generate_debug_for_page, run_cook_job

    cook_job_id = payload.get("cook_job_id")

    def _from_cook() -> dict:
        with SessionLocal() as db:
            return run_cook_job(db, UUID(cook_job_id))

    def _from_page() -> dict:
        document_id = UUID(payload["document_id"])
        page_number = int(payload.get("page_number") or 0)
        from app.services.open_response import plan_debug_cook_yield

        count = plan_debug_cook_yield(payload.get("count"))
        with SessionLocal() as db:
            from app.services.retrieval import fetch_chunks_for_page_range

            chunks = fetch_chunks_for_page_range(
                db,
                document_ids=[document_id],
                page_start=page_number,
                page_end=page_number,
            )
            page_text = "\n\n".join(
                c.get("text", "") for c in filter(lambda c: c.get("text"), chunks)
            ).strip()

            def _empty() -> dict:
                return {"document_id": str(document_id), "page_number": page_number, "saved": 0}

            def _generate() -> dict:
                saved = generate_debug_for_page(
                    db, document_id, page_number=page_number, page_text=page_text, count=count
                )
                return {
                    "document_id": str(document_id),
                    "page_number": page_number,
                    "saved": saved,
                }

            return pick(not page_text, _empty, _generate)

    return pick(bool(cook_job_id), _from_cook, _from_page)
