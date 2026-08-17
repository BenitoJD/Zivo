"""Runtime + plumbing engines: rule tables, no if-token."""

from __future__ import annotations

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.auth_gate import evaluate_auth_gate
from app.services.http_outcome import evaluate_http_outcome
from app.services.item_health import evaluate_item_health
from app.services.job_lifecycle import evaluate_job_lifecycle
from app.services.llm_route import evaluate_llm_route
from app.services.parse_detect import evaluate_parse_detect
from app.services.presence import evaluate_presence


def test_first_match_and_apply() -> None:
    rules = (
        Rule(when=(Pred("n", "lt", 3),), action="min_items"),
        Rule(when=(Pred("p", "gte", 0.95),), action="mastery"),
        Rule(when=(), action="continue"),
    )
    assert first_match(rules, {"n": 1, "p": 0.99}).action == "min_items"
    assert first_match(rules, {"n": 5, "p": 0.99}).action == "mastery"
    assert first_match(rules, {"n": 5, "p": 0.2}).action == "continue"
    assert apply("mastery", {"mastery": lambda: "stop", "continue": lambda: "go"}) == "stop"
    assert choose(True, "a", "b") == "a"
    assert pick(False, lambda: 1, lambda: 2) == 2
    calls: list[str] = []
    assert pick(True, lambda: calls.append("t") or 1, lambda: calls.append("f") or 2) == 1
    assert calls == ["t"]


def test_presence_auth_http() -> None:
    assert evaluate_presence(None).action == "missing"
    assert evaluate_presence("").action == "empty"
    assert evaluate_presence("ok").action == "ok"
    assert evaluate_auth_gate(account=None).action == "redirect"
    assert evaluate_auth_gate(account=None, allow_guest=True).action == "guest"
    assert evaluate_auth_gate(account=object(), is_admin_route=True, is_admin=False).action == "deny"
    assert evaluate_auth_gate(account=object()).action == "allow"
    assert evaluate_http_outcome("missing").status == 404
    assert evaluate_http_outcome("allow").status == 200


def test_parse_job_llm() -> None:
    assert evaluate_parse_detect(header=b"%PDF-1.4", filename="a.pdf").action == "pdf"
    assert evaluate_parse_detect(header=b"PK\x03\x04", filename="a.docx").action == "docx"
    assert evaluate_parse_detect(filename="note.md").action == "text"
    assert evaluate_job_lifecycle(status="running", stale_running=True).action == "reclaim"
    assert evaluate_job_lifecycle(status="queued", stale_queued=True).action == "fail"
    assert evaluate_job_lifecycle(status="failed", retries_left=True).action == "retry"
    assert evaluate_llm_route(has_primary=False, has_fallback=True).action == "use_fallback"
    assert evaluate_llm_route(has_primary=True).action == "use_primary"


def test_item_health_rule_table() -> None:
    keep = evaluate_item_health(p_correct=0.5, n_exposure=5, r_pbis=0.4)
    assert keep.action == "keep"
    retire = evaluate_item_health(p_correct=0.99, n_exposure=80)
    assert retire.action in {"flag", "retire"}


def test_rag_ready_cook_spawn() -> None:
    from app.services.session_design import (
        evaluate_newspaper_edition_dispatch,
        evaluate_page_batch_enqueue,
        evaluate_pool_wake,
        evaluate_rag_ready_cook_spawn,
        evaluate_refill_dispatch,
    )

    assert evaluate_rag_ready_cook_spawn(background_prep=True) == "skip"
    assert evaluate_rag_ready_cook_spawn(background_prep=False) == "enqueue"
    assert evaluate_refill_dispatch(batch=0) == "skip"
    assert evaluate_refill_dispatch(batch=3) == "enqueue"
    assert evaluate_page_batch_enqueue(remaining=0, is_current=True, active=False) == "clear_pending"
    assert evaluate_page_batch_enqueue(remaining=0, is_current=False, active=False) == "skip"
    assert evaluate_page_batch_enqueue(remaining=4, is_current=True, active=True) == "mark_pending"
    assert evaluate_page_batch_enqueue(remaining=4, is_current=False, active=True) == "skip"
    assert evaluate_page_batch_enqueue(remaining=4, is_current=True, active=False) == "enqueue"
    assert evaluate_pool_wake(missing=True, ready=False, no_searchable_text=False) == "skip"
    assert evaluate_pool_wake(missing=False, ready=True, no_searchable_text=True) == "skip"
    assert evaluate_pool_wake(missing=False, ready=True, no_searchable_text=False) == "ok"
    assert evaluate_newspaper_edition_dispatch(action="cook", page=2) == "cook"
    assert evaluate_newspaper_edition_dispatch(action="cook", page=None) == "ingest_only"
    assert evaluate_newspaper_edition_dispatch(action="idle", page=1) == "ingest_only"
