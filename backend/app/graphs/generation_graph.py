"""Page-scoped MCQ generation from indexed PDF text."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.graphs.page_triage_graph import run_page_triage
from app.models import Document
from app.repositories.intel import update_activity
from app.services.aspect_discovery import (
    next_unasked,
    plan_cook_target_fallback,
    plan_stalled_aspect_keys,
    speculative_targets,
)
from app.services.mcq_dedup import prior_mcq_from_payload
from app.services.mcq_quality import generate_quality_mcq_batch
from app.services.presence import evaluate_presence
from app.services.question_budget import exceeds_page_budget
from app.services.question_pool import (
    clear_stale_coverage_complete,
    bump_aspect_attempts,
    get_page_coverage,
    get_question_budget,
    mark_aspects_asked,
    on_batch_completed,
    save_page_coverage,
    set_coverage_complete,
)
from app.services.retrieval import fetch_chunks_for_page_range, search_chunks
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens
from app.services.tutor_retrieval import plan_cook_aspect_hint_context

logger = logging.getLogger(__name__)

_PAGE_CONTEXT_MAX_TOKENS = PAGE_INPUT_MAX_TOKENS

_GEN_MODE_RULES = (
    Rule(when=(Pred("mode", "eq", "page_triage"),), action="page_triage"),
    Rule(when=(Pred("mode", "eq", "page_batch"),), action="page_batch"),
    Rule(when=(), action="unknown"),
)

_SKIP_CLONE_KEYS = frozenset({"artifact_id", "sequence", "quality"})


def _stable_page_context(page_text: str) -> str:
    """Byte-stable truncated page text: cacheable LLM prefix across batches."""
    return truncate_to_tokens(page_text, _PAGE_CONTEXT_MAX_TOKENS)


def get_cacheable_page_context(
    db: Session, document_id: uuid.UUID, page_number: int, page_text: str
) -> str:
    """Stable truncated page text, DB-cached so it survives worker restart and
    is shared across the 4 CPU workers.

    Replaces the old in-process dict: every worker used to compute and hold its
    own copy, and a crash dropped it, forcing the next batch on that page to
    re-truncate (cheap) but also lose provider-prompt-cache prefix stability
    hints across process boundaries. The DB cache is keyed per (doc, page).
    """
    from app.services.generation_cache import get as cache_get, page_context_key, put as cache_put

    key = page_context_key(document_id, page_number)
    cached = cache_get(db, kind="page_context", cache_key=key)

    def _miss() -> str:
        ctx = _stable_page_context(page_text)
        cache_put(db, kind="page_context", cache_key=key, value=ctx)
        return ctx

    return pick(bool(cached), lambda: str(cached), _miss)


def _aspect_retrieval_hints(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    targets: list[dict[str, Any]],
    page_text: str,
) -> str:
    """Short per-aspect retrieval snippets for the variable tail message (not cached prefix)."""

    def _hints() -> str:
        try:
            labels = [str(t.get("label") or t.get("key") or "") for t in targets]
            from app.services.embed import embed_query

            vecs = [embed_query(label) for label in filter(lambda label: label.strip(), labels)]

            def _search() -> str:
                dims = len(vecs[0])
                mean_vec = [sum(v[i] for v in vecs) / len(vecs) for i in range(dims)]
                hint = plan_cook_aspect_hint_context()
                hits = search_chunks(
                    db,
                    document_ids=[document_id],
                    query_embedding=mean_vec,
                    page_start=page_number,
                    page_end=page_number,
                    limit=hint.hit_limit,
                )
                parts: list[str] = []
                seen: set[str] = set()
                for chunk in hits:
                    t = (chunk.get("text") or "").strip()

                    def _add(text: str = t) -> None:
                        seen.add(text)
                        parts.append(text[: hint.snippet_chars])

                    pick(not t or t in seen, lambda: None, _add)
                return "\n---\n".join(parts)

            return apply(
                evaluate_presence(vecs).action,
                {"ok": _search, "empty": lambda: "", "missing": lambda: ""},
            )
        except Exception:
            return ""

    return apply(
        evaluate_presence(targets and page_text).action,
        {"ok": _hints, "empty": lambda: "", "missing": lambda: ""},
    )


def _page_content_hash(page_text: str) -> str:
    return hashlib.sha256((page_text or "").encode("utf-8")).hexdigest()


def _reusable_mcq_payloads(
    db: Session,
    *,
    page_hash: str,
    page_number: int,
    limit: int,
    exclude_artifact_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Reuse MCQs from other documents with identical page text."""
    from app.config import get_settings
    from app.services.question_graph import plan_mcq_reuse

    reuse = plan_mcq_reuse(get_settings().mcq_reuse_scope)

    def _fetch() -> list[dict[str, Any]]:
        demo_filter = choose(reuse.demo_only, "AND d.account_id IS NULL", "")
        rows = db.execute(
            text(
                f"""
                SELECT a.payload
                FROM intel.assertion a
                JOIN qb.documents d ON d.id::text = a.payload->>'artifact_id'
                WHERE a.status = 'active'
                  AND a.payload->>'page_content_hash' = :page_hash
                  AND a.payload->>'artifact_id' != :exclude_artifact
                  {demo_filter}
                ORDER BY a.payload->>'artifact_id', (a.payload->>'page_number')::int,
                         (a.payload->>'sequence')::int ASC
                LIMIT :limit
                """
            ),
            {
                "page_hash": page_hash,
                "limit": limit,
                "exclude_artifact": str(exclude_artifact_id),
            },
        ).scalars().all()
        from app.services.question_graph import filter_reusable_templates

        parsed: list[dict[str, Any]] = []
        for raw in rows:
            payload = pick(isinstance(raw, dict), lambda r=raw: r, lambda r=raw: json.loads(r))
            parsed.append(payload)
        return list(filter_reusable_templates(parsed))

    return pick(not reuse.enabled, lambda: [], _fetch)


def _clone_reusable_mcqs(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    page_hash: str,
    targets: list[dict[str, Any]],
    start_sequence: int,
    budget: int,
) -> int:
    from app.services.quality_evaluation import evaluate_clone_template

    reusable = _reusable_mcq_payloads(
        db,
        page_hash=page_hash,
        page_number=page_number,
        limit=len(targets),
        exclude_artifact_id=document_id,
    )

    def _clone() -> int:
        box: dict[str, Any] = {
            "saved": 0,
            "sequence": start_sequence,
            "stop": False,
            "asked_keys": [],
            "payloads": [],
        }
        for target, template in zip(targets, reusable):
            def _step(tgt: dict[str, Any] = target, tmpl: dict[str, Any] = template) -> None:
                draft = {
                    "question": tmpl.get("question") or tmpl.get("stem"),
                    "options": tmpl.get("options") or [],
                    "correct_index": tmpl.get("correct_index"),
                    "correct_indices": tmpl.get("correct_indices"),
                }
                verdict = evaluate_clone_template(draft)

                def _keep() -> None:
                    box["sequence"] += 1
                    seq = box["sequence"]

                    def _over() -> None:
                        box["stop"] = True

                    def _maybe() -> None:
                        def _save() -> None:
                            payload = dict(
                                filter(
                                    lambda kv: kv[0] not in _SKIP_CLONE_KEYS,
                                    tmpl.items(),
                                )
                            )
                            payload["page_content_hash"] = page_hash
                            payload["reused_from_shared"] = True
                            payload["quality"] = {
                                "decision": verdict.decision,
                                "policy_version": verdict.policy_version,
                                "reuse_attested": True,
                            }
                            box["payloads"].append((payload, seq))
                            pick(
                                bool(tgt and tgt.get("key")),
                                lambda: box["asked_keys"].append(str(tgt["key"])),
                                lambda: None,
                            )
                            box["saved"] += 1

                        pick(
                            _assertion_sequence_exists(db, document_id, page_number, seq),
                            lambda: None,
                            _save,
                        )

                    pick(exceeds_page_budget(seq, budget), _over, _maybe)

                apply(
                    verdict.decision,
                    {"fail": lambda: None, "pass": _keep, "revise": _keep},
                )

            pick(box["stop"], lambda: None, _step)

        pick(
            bool(box["payloads"]),
            lambda: _persist_assertions(
                db,
                document_id,
                box["payloads"],
                page_number=page_number,
                facet_rows=[],
            ),
            lambda: None,
        )
        pick(
            bool(box["asked_keys"]),
            lambda: mark_aspects_asked(db, document_id, page_number, box["asked_keys"]),
            lambda: None,
        )

        def _ckpt() -> None:
            from app.services.generation_checkpoint import checkpoint_after_save

            checkpoint_after_save(
                db,
                page_number=page_number,
                sequence=box["sequence"],
                saved_total=box["saved"],
            )

        pick(bool(box["saved"]), _ckpt, lambda: None)
        return int(box["saved"])

    return apply(
        evaluate_presence(reusable).action,
        {"ok": _clone, "empty": lambda: 0, "missing": lambda: 0},
    )


def run_generation(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    mode = options.get("mode", "page_batch")
    hit = first_match(_GEN_MODE_RULES, {"mode": mode})
    return apply(
        hit.action,
        {
            "page_triage": lambda: run_page_triage(
                db,
                document_id,
                page_number=int(options["page_number"]),
                activity_id=options.get("activity_id"),
                precompute=bool(options.get("precompute")),
            ),
            "page_batch": lambda: _run_page_batch(db, document_id, options),
            "unknown": lambda: {
                "questions_saved": 0,
                "error": f"unknown generation mode: {mode}",
            },
        },
    )


def _assertion_sequence_exists(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    sequence: int,
    *,
    serve_mode: str = "learn",
) -> bool:
    exists = db.execute(
        text(
            """
            SELECT 1 FROM intel.assertion
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND (payload->>'page_number')::int = :page
              AND (payload->>'sequence')::int = :sequence
              AND COALESCE(payload->>'serve_mode', 'learn') = :serve_mode
            LIMIT 1
            """
        ),
        {
            "artifact_id": str(document_id),
            "page": page_number,
            "sequence": sequence,
            "serve_mode": serve_mode,
        },
    ).scalar()
    return exists is not None


def _prior_mcqs_on_page(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    *,
    serve_mode: str = "learn",
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT payload FROM intel.assertion
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND (payload->>'page_number')::int = :page
              AND COALESCE(payload->>'serve_mode', 'learn') = :serve_mode
            ORDER BY (payload->>'sequence')::int ASC
            """
        ),
        {
            "artifact_id": str(document_id),
            "page": page_number,
            "serve_mode": serve_mode,
        },
    ).scalars().all()
    prior: list[dict[str, Any]] = []
    for raw in rows:
        payload = pick(isinstance(raw, dict), lambda r=raw: r, lambda r=raw: json.loads(r))
        prior.append(prior_mcq_from_payload(payload))
    return prior


def _next_aspects(
    doc: Document, page_number: int, n: int, *, cook_mode: str = "learn"
) -> list[dict[str, Any]]:
    """The next up-to-n aspects on this page that have not been asked yet."""
    from app.services.question_budget import parse_budget_mode
    from app.services.kc_coverage import coverage_aspects_field

    mode = parse_budget_mode(cook_mode)
    cov = get_page_coverage(doc, page_number)
    aspect_field = coverage_aspects_field(mode)
    return list(next_unasked(cov.get(aspect_field) or cov.get("aspects") or [], n=n).aspects)


def _speculative_aspects(page_text: str, page_number: int, n: int) -> list[dict[str, Any]]:
    """Lightweight aspect targets when triage hasn't landed yet.

    Aspect Discovery Engine owns the pick. Real triage overwrites page_coverage
    later and refines the plan for subsequent batches.
    """
    return list(speculative_targets(page_text, page_number, n).aspects)


def _run_page_batch(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    from app.services.generation_checkpoint import checkpoint_after_save, resolve_start_sequence

    activity_id = options.get("activity_id")
    page_number = int(options["page_number"])
    batch_size = int(options.get("batch_size", 5))
    cook_mode = str(options.get("cook_mode") or "learn")
    from app.services.question_budget import parse_budget_mode

    serve_mode = parse_budget_mode(cook_mode)
    start_sequence = resolve_start_sequence(
        db,
        document_id,
        page_number=page_number,
        options=options,
    )

    def _empty_doc() -> dict[str, Any]:
        return {"questions_saved": 0, "page_number": page_number}

    def _with_doc(doc: Document) -> dict[str, Any]:
        from app.services.question_pool import is_non_content_page

        def _non_content() -> dict[str, Any]:
            set_coverage_complete(db, document_id, page_number)
            on_batch_completed(db, document_id, page=page_number, saved=0)
            pick(
                bool(activity_id),
                lambda: update_activity(
                    db,
                    uuid.UUID(str(activity_id)),
                    status="succeeded",
                    stats={
                        "questions_saved": 0,
                        "page_number": page_number,
                        "non_content": True,
                    },
                    finished=True,
                ),
                lambda: None,
            )
            db.commit()
            return {"questions_saved": 0, "page_number": page_number, "non_content": True}

        def _indexed() -> dict[str, Any]:
            chunks = fetch_chunks_for_page_range(
                db,
                document_ids=[document_id],
                page_start=page_number,
                page_end=page_number,
            )
            page_text = "\n\n".join(
                c["text"] for c in filter(lambda c: c.get("text"), chunks)
            ).strip()

            def _not_indexed() -> dict[str, Any]:
                on_batch_completed(db, document_id, page=page_number, saved=0)
                db.commit()
                return {
                    "questions_saved": 0,
                    "page_number": page_number,
                    "error": "page_not_indexed",
                }

            def _cook_page() -> dict[str, Any]:
                def _newspaper() -> dict[str, Any] | None:
                    from app.services.content_worthiness import plan_newspaper_batch_gate

                    skip = plan_newspaper_batch_gate(page_text=page_text, db=db)

                    def _skip_page() -> dict[str, Any]:
                        save_page_coverage(
                            db,
                            document_id,
                            page=page_number,
                            question_budget=skip.question_budget,
                            aspects=[],
                            rationale=(
                                f"Newspaper filter ({skip.reason}): {skip.details or skip.reason}"
                            ),
                            content_type="non_content",
                            non_content=skip.non_content,
                            programmable=False,
                        )
                        pick(
                            skip.mark_complete,
                            lambda: set_coverage_complete(db, document_id, page_number),
                            lambda: None,
                        )
                        on_batch_completed(db, document_id, page=page_number, saved=0)
                        pick(
                            bool(activity_id),
                            lambda: update_activity(
                                db,
                                uuid.UUID(str(activity_id)),
                                status="succeeded",
                                stats={
                                    "questions_saved": 0,
                                    "page_number": page_number,
                                    "newspaper_filter": skip.reason,
                                    "non_content": True,
                                },
                                finished=True,
                            ),
                            lambda: None,
                        )
                        db.commit()
                        return {
                            "questions_saved": 0,
                            "page_number": page_number,
                            "non_content": True,
                            "newspaper_filter": skip.reason,
                        }

                    return apply(skip.action, {"skip": _skip_page, "cook": lambda: None})

                skipped = pick(
                    bool((doc.meta or {}).get("newspaper")),
                    _newspaper,
                    lambda: None,
                )
                return pick(skipped is not None, lambda: skipped, lambda: _cook_after_gate(doc, page_text))

            return apply(
                evaluate_presence(page_text).action,
                {"ok": _cook_page, "empty": _not_indexed, "missing": _not_indexed},
            )

        return pick(is_non_content_page(doc, page_number), _non_content, _indexed)

    def _cook_after_gate(doc: Document, page_text: str) -> dict[str, Any]:
        clear_stale_coverage_complete(db, document_id, page_number)
        doc = db.get(Document, document_id)

        def _cook(live: Document) -> dict[str, Any]:
            budget = get_question_budget(live, page_number, mode=serve_mode)
            remaining = budget - start_sequence
            n = min(batch_size, max(0, remaining))
            unasked = _next_aspects(live, page_number, n, cook_mode=cook_mode)
            coverage = get_page_coverage(live, page_number)
            cook_plan = plan_cook_target_fallback(
                unasked=unasked,
                has_aspects=bool(coverage.get("aspects")),
            )
            cook_plan = apply(
                cook_plan.action,
                {
                    "try_speculative": lambda: plan_cook_target_fallback(
                        unasked=(),
                        has_aspects=bool(coverage.get("aspects")),
                        speculative=_speculative_aspects(page_text, page_number, n),
                    ),
                    "close_coverage": lambda: cook_plan,
                    "cook": lambda: cook_plan,
                    "none": lambda: cook_plan,
                },
            )

            def _close() -> dict[str, Any]:
                set_coverage_complete(db, document_id, page_number)
                on_batch_completed(db, document_id, page=page_number, saved=0)
                pick(
                    bool(activity_id),
                    lambda: update_activity(
                        db,
                        uuid.UUID(str(activity_id)),
                        status="succeeded",
                        stats={"questions_saved": 0, "page_number": page_number},
                        finished=True,
                    ),
                    lambda: None,
                )
                db.commit()
                return {"questions_saved": 0, "page_number": page_number}

            def _no_targets() -> dict[str, Any]:
                on_batch_completed(db, document_id, page=page_number, saved=0)
                db.commit()
                return {"questions_saved": 0, "page_number": page_number, "error": "no_targets"}

            def _cook_targets() -> dict[str, Any]:
                targets = list(cook_plan.targets)
                prior_mcqs = _prior_mcqs_on_page(
                    db, document_id, page_number, serve_mode=serve_mode
                )
                page_hash = _page_content_hash(page_text)
                from app.services.page_lessons import cook_page_lesson, should_cook_lesson

                cov_aspects = get_page_coverage(live, page_number).get("aspects") or targets

                def _lesson() -> None:
                    try:
                        cook_page_lesson(
                            db,
                            document_id,
                            page=page_number,
                            page_text=page_text,
                            aspects=cov_aspects,
                            content_hash=page_hash,
                        )
                    except Exception:
                        logger.warning(
                            "lesson cook failed doc=%s page=%s",
                            document_id,
                            page_number,
                            exc_info=True,
                        )

                pick(
                    should_cook_lesson(serve_mode=serve_mode, aspects=cov_aspects),
                    _lesson,
                    lambda: None,
                )
                cloned = _clone_reusable_mcqs(
                    db,
                    document_id,
                    page_number=page_number,
                    page_hash=page_hash,
                    targets=targets,
                    start_sequence=start_sequence,
                    budget=budget,
                )

                def _reused() -> dict[str, Any]:
                    on_batch_completed(db, document_id, page=page_number, saved=cloned)
                    pick(
                        bool(activity_id),
                        lambda: update_activity(
                            db,
                            uuid.UUID(str(activity_id)),
                            status="succeeded",
                            stats={
                                "questions_saved": cloned,
                                "page_number": page_number,
                                "reused": True,
                            },
                            finished=True,
                        ),
                        lambda: None,
                    )
                    db.commit()
                    return {
                        "questions_saved": cloned,
                        "page_number": page_number,
                        "reused": True,
                    }

                def _generate() -> dict[str, Any]:
                    stable_context = get_cacheable_page_context(
                        db, document_id, page_number, page_text
                    )
                    aspect_hints = _aspect_retrieval_hints(
                        db, document_id, page_number, targets, page_text
                    )
                    from app.services.quality_evaluation import resolve_cook_content_type

                    content_type = resolve_cook_content_type(
                        newspaper=bool((live.meta or {}).get("newspaper")),
                        coverage_type=get_page_coverage(live, page_number).get("content_type"),
                    )
                    state = {"saved": 0, "sequence": start_sequence, "hit_budget": False}
                    asked_keys: list[str] = []
                    pinned_qid = options.get("practice_qid")

                    def _on_accept(target: dict[str, Any], payload: dict[str, Any]) -> bool:
                        state["sequence"] += 1
                        seq = state["sequence"]

                        def _over() -> bool:
                            state["hit_budget"] = True
                            return False

                        def _rest() -> bool:
                            def _taken() -> bool:
                                return True

                            def _save() -> bool:
                                p = dict(payload)
                                p["page_content_hash"] = page_hash
                                p["serve_mode"] = serve_mode
                                _persist_assertions(
                                    db,
                                    document_id,
                                    [(p, seq)],
                                    page_number=page_number,
                                    facet_rows=[],
                                    pinned_qid=pinned_qid,
                                    serve_mode=serve_mode,
                                )
                                db.commit()
                                pick(
                                    bool(target and target.get("key")),
                                    lambda: asked_keys.append(str(target["key"])),
                                    lambda: None,
                                )
                                state["saved"] += 1
                                return True

                            return pick(
                                _assertion_sequence_exists(
                                    db, document_id, page_number, seq, serve_mode=serve_mode
                                ),
                                _taken,
                                _save,
                            )

                        return pick(exceeds_page_budget(seq, budget), _over, _rest)

                    generate_quality_mcq_batch(
                        db,
                        page_text=stable_context,
                        page_number=page_number,
                        targets=targets,
                        prior_mcqs=prior_mcqs,
                        aspect_hints=aspect_hints,
                        content_type=content_type,
                        on_accept=_on_accept,
                    )

                    totals = {
                        "saved": state["saved"],
                        "sequence": state["sequence"],
                        "hit_budget": state["hit_budget"],
                    }
                    pick(
                        bool(asked_keys),
                        lambda: mark_aspects_asked(
                            db, document_id, page_number, asked_keys, cook_mode=cook_mode
                        ),
                        lambda: None,
                    )
                    pick(
                        bool(totals["saved"]),
                        lambda: checkpoint_after_save(
                            db,
                            page_number=page_number,
                            sequence=totals["sequence"],
                            saved_total=totals["saved"],
                        ),
                        lambda: None,
                    )
                    from app.services.session_design import (
                        evaluate_empty_batch_coverage_close,
                        evaluate_generation_batch_outcome,
                        evaluate_learn_cook_coverage_close,
                        evaluate_serial_cook_fallback,
                    )

                    pick(
                        evaluate_learn_cook_coverage_close(
                            hit_budget=totals["hit_budget"], serve_mode=serve_mode
                        ),
                        lambda: set_coverage_complete(db, document_id, page_number),
                        lambda: None,
                    )

                    def _serial() -> None:
                        from app.services.mcq_quality import generate_quality_mcq
                        from app.services.mcq_dedup import prior_mcq_from_payload as prior_from

                        serial = {
                            "sequence": totals["sequence"],
                            "saved": totals["saved"],
                            "stop": False,
                            "priors": list(prior_mcqs or []),
                        }
                        for target in targets:
                            def _one(tgt: dict[str, Any] = target) -> None:
                                def _try() -> None:
                                    serial["sequence"] += 1
                                    seq = serial["sequence"]

                                    def _over() -> None:
                                        serial["stop"] = True

                                    def _maybe() -> None:
                                        def _gen() -> None:
                                            payload = generate_quality_mcq(
                                                db,
                                                page_text=stable_context,
                                                page_number=page_number,
                                                sequence=seq,
                                                target_aspect=tgt,
                                                prior_mcqs=serial["priors"],
                                                content_type=content_type,
                                            )

                                            def _save() -> None:
                                                payload2 = dict(payload)
                                                payload2["page_content_hash"] = page_hash
                                                payload2["serve_mode"] = serve_mode
                                                _persist_assertion(
                                                    db,
                                                    document_id,
                                                    payload2,
                                                    page_number=page_number,
                                                    sequence=seq,
                                                    serve_mode=serve_mode,
                                                )
                                                pick(
                                                    bool(tgt.get("key")),
                                                    lambda: mark_aspects_asked(
                                                        db,
                                                        document_id,
                                                        page_number,
                                                        [str(tgt["key"])],
                                                        cook_mode=cook_mode,
                                                    ),
                                                    lambda: None,
                                                )
                                                serial["saved"] += 1
                                                serial["priors"] = list(serial["priors"]) + [
                                                    prior_from(payload2)
                                                ]
                                                checkpoint_after_save(
                                                    db,
                                                    page_number=page_number,
                                                    sequence=seq,
                                                    saved_total=serial["saved"],
                                                )

                                            pick(not payload, lambda: None, _save)

                                        pick(
                                            _assertion_sequence_exists(
                                                db,
                                                document_id,
                                                page_number,
                                                seq,
                                                serve_mode=serve_mode,
                                            ),
                                            lambda: None,
                                            _gen,
                                        )

                                    pick(exceeds_page_budget(seq, budget), _over, _maybe)

                                pick(
                                    serial["stop"] or bool(tgt.get("asked")),
                                    lambda: None,
                                    _try,
                                )

                            _one()
                        totals["saved"] = serial["saved"]
                        totals["sequence"] = serial["sequence"]

                    pick(
                        evaluate_serial_cook_fallback(
                            saved=totals["saved"], has_targets=bool(targets)
                        ),
                        _serial,
                        lambda: None,
                    )
                    pick(
                        evaluate_empty_batch_coverage_close(
                            saved=totals["saved"],
                            start_sequence=start_sequence,
                            batch_size=batch_size,
                            budget=budget,
                            has_targets=bool(targets),
                            has_aspects=bool(
                                get_page_coverage(live, page_number).get("aspects")
                            ),
                        ),
                        lambda: set_coverage_complete(db, document_id, page_number),
                        lambda: None,
                    )
                    db.refresh(live)
                    from app.services.kc_coverage import coverage_aspects_field

                    asked_now = {
                        str(a.get("key"))
                        for a in filter(
                            lambda a: a.get("asked"),
                            get_page_coverage(live, page_number).get(
                                coverage_aspects_field(serve_mode)
                            )
                            or [],
                        )
                    }
                    still_unasked = plan_stalled_aspect_keys(targets, list(asked_now))
                    pick(
                        bool(still_unasked),
                        lambda: bump_aspect_attempts(
                            db, document_id, page_number, still_unasked
                        ),
                        lambda: None,
                    )
                    on_batch_completed(db, document_id, page=page_number, saved=totals["saved"])
                    outcome = evaluate_generation_batch_outcome(
                        saved=totals["saved"], has_targets=bool(targets)
                    )
                    pick(
                        bool(activity_id),
                        lambda: update_activity(
                            db,
                            uuid.UUID(str(activity_id)),
                            status=outcome.activity_status,
                            stats={
                                "questions_saved": totals["saved"],
                                "page_number": page_number,
                            },
                            finished=True,
                            error_summary=outcome.error_summary,
                        ),
                        lambda: None,
                    )
                    db.commit()
                    return pick(
                        outcome.error_code == "generation_empty",
                        lambda: {
                            "questions_saved": 0,
                            "page_number": page_number,
                            "error": "generation_empty",
                        },
                        lambda: {
                            "questions_saved": totals["saved"],
                            "page_number": page_number,
                        },
                    )

                return pick(cloned >= len(targets), _reused, _generate)

            return apply(
                cook_plan.action,
                {
                    "close_coverage": _close,
                    "cook": _cook_targets,
                    "none": _no_targets,
                    "try_speculative": _no_targets,
                },
            )

        return apply(
            evaluate_presence(doc).action,
            {"ok": lambda: _cook(doc), "empty": _empty_doc, "missing": _empty_doc},
        )

    doc = db.get(Document, document_id)
    return apply(
        evaluate_presence(doc).action,
        {"ok": lambda: _with_doc(doc), "empty": _empty_doc, "missing": _empty_doc},
    )


def _link_assertion_concepts(
    db: Session,
    assertion_id: uuid.UUID,
    payload: dict[str, Any],
    pinned_qid: str | None = None,
) -> None:
    """Link an assertion to the Wikidata concepts it tests (intel.assertion_participant).

    Sources concepts from payload['tested_concepts'] (emitted by the LLM) and
    optionally a pinned concept from the generation job (practice-library mode).
    Idempotent. Failures are non-fatal: tagging never blocks question creation.
    """
    try:
        from app.repositories.intel import (
            get_or_create_concept_entity,
            link_assertion_concept,
        )
    except Exception:
        logger.debug("concept-linking unavailable; skipping tagging", exc_info=True)
        return

    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for concept in payload.get("tested_concepts") or []:
        qid = str(concept.get("qid") or "").strip().upper()
        label = str(concept.get("label") or "").strip()

        def _add(q: str = qid, lab: str = label) -> None:
            candidates.append({"qid": q, "label": lab})
            seen.add(q)

        pick(bool(qid) and qid not in seen and bool(label), _add, lambda: None)

    def _pin() -> None:
        qid = pinned_qid.strip().upper()

        def _add_pin() -> None:
            candidates.append(
                {"qid": qid, "label": payload.get("primary_concept") or qid}
            )
            seen.add(qid)

        pick(qid not in seen, _add_pin, lambda: None)

    pick(bool(pinned_qid), _pin, lambda: None)

    for concept in candidates:
        try:
            entity_id = get_or_create_concept_entity(db, concept["qid"], concept["label"])
            link_assertion_concept(db, assertion_id, entity_id)
        except Exception:
            continue


def _seed_birth_difficulty(db: Session, assertion_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Seed an item's birth-time difficulty prior at generation (flag-gated).

    Off the answer path; gives difficulty_edge a non-coin-flip starting point on
    answered-once items. Best-effort: a prior is an optimization, so a failure here
    must never roll back the assertion that was already created.
    """
    from app.config import get_settings

    def _seed() -> None:
        try:
            from app.services.calibration import birth_difficulty_prior, seed_item_difficulty

            seed_item_difficulty(db, assertion_id, birth_difficulty_prior(payload).difficulty)
        except Exception:
            logger.debug(
                "birth-difficulty seeding failed for assertion %s", assertion_id, exc_info=True
            )

    pick(get_settings().difficulty_prior_enabled, _seed, lambda: None)


def _persist_assertion(
    db: Session,
    document_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    page_number: int,
    sequence: int,
    serve_mode: str = "learn",
) -> None:
    from app.repositories.intel import _concept_id, _source_id

    assertion_id = uuid.uuid4()
    payload = {
        **payload,
        "artifact_id": str(document_id),
        "format": "qb.mcq.v1",
        "page_number": page_number,
        "sequence": sequence,
        "serve_mode": serve_mode,
    }
    db.execute(
        text(
            """
            INSERT INTO intel.assertion (
              id, type_concept_id, source_id, canonical_uri, fingerprint,
              title, summary, payload, status
            )
            VALUES (
              :id, :type_id, :source_id, :uri, :fp,
              :title, :summary, CAST(:payload AS jsonb), 'active'
            )
            """
        ),
        {
            "id": assertion_id,
            "type_id": _concept_id(db, "/vocab/assertion/question.mcq"),
            "source_id": _source_id(db, "user-upload"),
            "uri": f"qb://assertion/{assertion_id}",
            "fp": f"{document_id}:{page_number}:{serve_mode}:{sequence}",
            "title": (payload.get("question") or "")[:200],
            "summary": payload.get("explanation"),
            "payload": json.dumps(payload),
        },
    )
    from app.services.mcq_assertion_facets import upsert_facet

    upsert_facet(
        db,
        assertion_id=assertion_id,
        artifact_id=document_id,
        page_number=page_number,
        sequence=sequence,
        payload=payload,
    )
    _link_assertion_concepts(db, assertion_id, payload)
    _seed_birth_difficulty(db, assertion_id, payload)


def _persist_assertions(
    db: Session,
    document_id: uuid.UUID,
    payloads: list[tuple[dict[str, Any], int]],
    *,
    page_number: int,
    facet_rows: list[dict[str, Any]] | None = None,
    pinned_qid: str | None = None,
    serve_mode: str = "learn",
) -> list[dict[str, Any]]:
    """Batched persist: one executemany INSERT for assertions + one for facets.

    Replaces N sequential _persist_assertion calls (each = 1 assertion INSERT +
    1 facet UPSERT = 2N round trips for a batch of N) with two batched writes.
    Concept/source ids are resolved once. Returns the finalized payload dicts
    (with assertion_id set) so callers can append them to prior_mcqs in memory.
    """
    from app.repositories.intel import _concept_id, _source_id
    from app.services.mcq_assertion_facets import upsert_facets

    def _write() -> list[dict[str, Any]]:
        type_id = _concept_id(db, "/vocab/assertion/question.mcq")
        source_id = _source_id(db, "user-upload")

        assertion_rows: list[dict[str, Any]] = []
        finalized: list[dict[str, Any]] = []
        facet_input: list[dict[str, Any]] = []
        for payload, sequence in payloads:
            assertion_id = uuid.uuid4()
            payload = {
                **payload,
                "artifact_id": str(document_id),
                "format": "qb.mcq.v1",
                "page_number": page_number,
                "sequence": sequence,
                "serve_mode": serve_mode,
            }
            assertion_rows.append(
                {
                    "id": assertion_id,
                    "type_id": type_id,
                    "source_id": source_id,
                    "uri": f"qb://assertion/{assertion_id}",
                    "fp": f"{document_id}:{page_number}:{serve_mode}:{sequence}",
                    "title": (payload.get("question") or "")[:200],
                    "summary": payload.get("explanation"),
                    "payload": json.dumps(payload),
                }
            )
            payload["_assertion_id"] = str(assertion_id)
            finalized.append(payload)
            facet_input.append(
                {
                    "assertion_id": assertion_id,
                    "artifact_id": document_id,
                    "page_number": page_number,
                    "sequence": sequence,
                    "payload": payload,
                }
            )

        db.execute(
            text(
                """
                INSERT INTO intel.assertion (
                  id, type_concept_id, source_id, canonical_uri, fingerprint,
                  title, summary, payload, status
                )
                VALUES (
                  :id, :type_id, :source_id, :uri, :fp,
                  :title, :summary, CAST(:payload AS jsonb), 'active'
                )
                """
            ),
            assertion_rows,
        )
        upsert_facets(db, facet_input)
        pick(facet_rows is not None, lambda: facet_rows.extend(facet_input), lambda: None)
        for payload in finalized:
            assertion_id_str = payload.get("_assertion_id")

            def _link(pid: str = assertion_id_str, pl: dict[str, Any] = payload) -> None:
                _link_assertion_concepts(db, uuid.UUID(pid), pl, pinned_qid)
                _seed_birth_difficulty(db, uuid.UUID(pid), pl)

            pick(bool(assertion_id_str), _link, lambda: None)
        _write_batch_lineage(db, finalized)
        return finalized

    return apply(
        evaluate_presence(payloads).action,
        {"ok": _write, "empty": lambda: [], "missing": lambda: []},
    )


def _write_batch_lineage(db: Session, finalized: list[dict[str, Any]]) -> None:
    """Link batch items so miss/hit routing has successors to follow.

    Plans edges via Question Graph Engine, then persists follow_up_after_miss and
    harder_than (same_concept is plan-only metadata today).
    """

    def _write() -> None:
        from app.repositories.intel import _concept_id
        from app.services.question_graph import (
            LINK_FOLLOW_UP_AFTER_MISS,
            LINK_HARDER_THAN,
            plan_batch_lineage,
        )

        follow_id = _concept_id(db, "/vocab/link/follow_up_after_miss")
        harder_id = _concept_id(db, "/vocab/link/harder_than")
        kind_to_id = {
            LINK_FOLLOW_UP_AFTER_MISS: follow_id,
            LINK_HARDER_THAN: harder_id,
        }
        plan = plan_batch_lineage(finalized)
        rows: list[dict[str, Any]] = []
        for edge in plan.edges:
            lt = kind_to_id.get(edge.kind)

            def _append(link_type: Any = lt, e: Any = edge) -> None:
                rows.append(
                    {
                        "f": uuid.UUID(e.from_id),
                        "t": uuid.UUID(e.to_id),
                        "lt": link_type,
                        "conf": e.confidence,
                    }
                )

            pick(lt is None, lambda: None, _append)

        def _insert() -> None:
            db.execute(
                text(
                    """
                    INSERT INTO intel.assertion_lineage
                      (from_assertion_id, to_assertion_id, link_type_concept_id, confidence)
                    VALUES (:f, :t, :lt, :conf)
                    ON CONFLICT DO NOTHING
                    """
                ),
                rows,
            )

        apply(
            evaluate_presence(rows).action,
            {"ok": _insert, "empty": lambda: None, "missing": lambda: None},
        )

    pick(len(finalized) < 2, lambda: None, _write)
