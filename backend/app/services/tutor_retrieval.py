"""Tutor Retrieval Engine - retrieve gate, RAG window size, chunk rank.

Design: docs/ENGINES.md (Tutor Retrieval)
Version: qb.tutor_retrieval.v1

Owns: whether a chat turn needs new retrieval, which pages enter the RAG
window, and how over-fetched chunks are ranked down to top_n.
Plumbing (embed, pgvector, cross-encoder model load, ingest status) stays
in retrieval / rag_window / rerank services.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from app.engine_runtime import Pred, Rule, choose, first_match, pick

TUTOR_RETRIEVAL_VERSION = "qb.tutor_retrieval.v1"
DEFAULT_POLICY = "tutor_retrieval_v1"

MAX_RAG_PAGES = 6
DEFAULT_TOP_N = 6
DEFAULT_FETCH_LIMIT = 20
# Grade-tutor context: how many ranked chunks to paste into the prompt.
GRADE_CONTEXT_TOP_N = 4
# Semantic response-cache reuse (GPTCache-style). Conservative near-paraphrase.
RESPONSE_CACHE_SIMILARITY = 0.92
# Brainstorm: evenly spaced whole-source sample (breadth, not top-k depth).
BRAINSTORM_CHUNK_SAMPLE = 24
# Indexed RAG chunk split (ingest): size + overlap for tutor and cook context.
RAG_CHUNK_MAX_CHARS = 900
RAG_CHUNK_OVERLAP = 120
# Cook-time aspect hints: vector hits + snippet trim.
COOK_ASPECT_HINT_HITS = 5
COOK_ASPECT_HINT_SNIPPET_CHARS = 600
# Topic Explain: vector hits (and fallback leading chunks) per topic.
TOPIC_EXPLAIN_CHUNK_LIMIT = 8
# Chat history: compress older turns once the thread exceeds this length.
CHAT_HISTORY_COMPRESS_ABOVE = 4
CHAT_HISTORY_KEEP_TAIL = 2
CHAT_HISTORY_SNIPPET_CHARS = 180
CODING_ASSIST_HISTORY = 12
CHAT_THREAD_HISTORY = 6
CODING_ASSIST_CODE_CHARS = 12_000

GateReason = Literal[
    "empty",
    "first_turn",
    "page_scope",
    "current_page",
    "selection",
    "continuation",
    "content_query",
    "brainstorm",
    "prefetched_reference",
]

_NO_RETRIEVE_RE = re.compile(
    r"^\s*("
    r"(?:thanks?|thank you|thx|ty|got it|ok(?:ay)?|k|cool|nice|great|awesome|perfect|"
    r"makes sense|understood|i see|right|yep|yeah|yes|no|sure|wow|huh|hmm|lol|haha)"
    r"|"
    r"(?:please (?:continue|go on|keep going|explain more)|continue|go on|keep going|"
    r"and then\??|next\??|more\??)"
    r"|"
    r"(?:explain (?:that|it|this) (?:again|more)|say that (?:again|differently)|"
    r"what do you mean|clarify|elaborate)"
    r")\s*[!.?]*\s*$",
    re.IGNORECASE,
)

_PAGE_REF_RE = re.compile(
    r"\b(?:on|at)\s+page\s+(\d+)(?:\s*[-–]\s*(\d+))?\b"
    r"|\bpages?\s+(\d+)\s*(?:to|through|and)\s*(\d+)\b"
    r"|\bpage\s+(\d+)\s*[-–]\s*(\d+)\b"
    r"|\bpage\s+(\d+)\b",
    re.IGNORECASE,
)

_QUIZ_RE = re.compile(
    r"\b(quiz|quizzes|mcq|mcqs|multiple[- ]choice|practice question|test me|"
    r"give me .* question|exam questions?)\b",
    re.IGNORECASE,
)
_MCQ_ANSWER_REQUEST_RE = re.compile(
    r"|".join(
        [
            r"\bwhat(?:'s| is) the (?:correct )?answer\b",
            r"\bwhich (?:option|choice) (?:is )?(?:correct|right)\b",
            r"\btell me (?:the )?answer\b",
            r"\bgive me (?:the )?answer\b",
            r"\bwhat should i (?:pick|choose|select)\b",
            r"\b(?:is|was) (?:option )?[a-d] (?:correct|right)\b",
            r"\bthe (?:correct|right) (?:option|choice|letter)\b",
            r"\breveal the answer\b",
        ]
    ),
    re.IGNORECASE,
)
_HIGHLIGHTED_RE = re.compile(
    r'I highlighted(?: this while studying)?:\s*\n\n"([^"]{1,240})"',
    re.IGNORECASE,
)
_WIKIPEDIA_PREFETCH_RE = re.compile(
    r"Wikipedia summary of\s+\"",
    re.IGNORECASE,
)
_CHAT_SURFACES = frozenset({"read", "learn", "test", "brainstorm", "socratic"})
CHAT_BUSY_MESSAGE = "Tutor is busy. Try again."
MCQ_ANSWER_GUARDRAIL = (
    "Tutor guardrail (this turn): The learner has not asked for the quiz answer. "
    "Explain the topic clearly. Do NOT state which option letter is correct, "
    'do NOT say "the correct answer is …", and do NOT map your explanation to A/B/C/D.'
)
PREFETCHED_REFERENCE_DIRECTIVE = (
    "Reference lookup (this turn): The learner highlighted a term from their current "
    "study question. Use the Wikipedia summary in their message plus the Learn session "
    "block. Explain the highlighted term in that quiz context only. Do not discuss "
    "unrelated topics from other newspaper pages or earlier chat turns."
)
_CHAT_ERROR_COPY = {
    "busy": CHAT_BUSY_MESSAGE,
    "rate": "Too many requests — wait a moment and try again.",
    "timeout": "That took too long — try a shorter question.",
}


@dataclass(frozen=True)
class RetrievalGateVerdict:
    retrieve: bool
    reason: GateReason
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


@dataclass(frozen=True)
class RagWindowPlan:
    pages: tuple[int, ...]
    current_page: int
    max_pages: int = MAX_RAG_PAGES
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


@dataclass(frozen=True)
class ChunkRankVerdict:
    chunks: tuple[dict, ...]
    top_n: int
    used_rerank: bool
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


@dataclass(frozen=True)
class PagePinVerdict:
    """Learn-mode: prefer current_page chunks before widening to RAG window."""

    pin_current_first: bool
    page: int | None = None
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


_POLICY_ALIASES = ("default", "tutor", "retrieval")
_RETRIEVE_RULES = (
    Rule(when=(Pred("empty", "truthy"),), action="empty", extras={"retrieve": False}),
    Rule(when=(Pred("brainstorm", "truthy"),), action="brainstorm", extras={"retrieve": False}),
    Rule(
        when=(Pred("prefetched", "truthy"),),
        action="prefetched_reference",
        extras={"retrieve": False},
    ),
    Rule(when=(Pred("no_history", "truthy"),), action="first_turn", extras={"retrieve": True}),
    Rule(when=(Pred("page_scope", "truthy"),), action="page_scope", extras={"retrieve": True}),
    Rule(when=(Pred("current_page", "truthy"),), action="current_page", extras={"retrieve": True}),
    Rule(when=(Pred("selection", "truthy"),), action="selection", extras={"retrieve": True}),
    Rule(
        when=(Pred("followup", "truthy"),),
        action="continuation",
        extras={"retrieve": False},
    ),
    Rule(when=(), action="content_query", extras={"retrieve": True}),
)
_CACHE_ELIGIBLE_RULES = (
    Rule(when=(Pred("include_image", "truthy"),), action="image"),
    Rule(when=(Pred("prefetched_reference", "truthy"),), action="prefetched_reference"),
    Rule(when=(Pred("selection_text", "truthy"),), action="selection"),
    Rule(when=(Pred("has_citations", "falsey"),), action="no_citations"),
    Rule(when=(Pred("multi_doc", "truthy"),), action="multi_doc"),
    Rule(when=(Pred("followup", "truthy"),), action="conversational_followup"),
    Rule(when=(), action="ok"),
)
_CHUNK_STRATEGY_RULES = (
    Rule(
        when=(Pred("pin_now", "truthy"),),
        action="pin_page",
        extras={"use_pin": True},
    ),
    Rule(
        when=(Pred("need_page_fetch", "truthy"),),
        action="page_range",
    ),
    Rule(
        when=(Pred("pinned_single", "truthy"), Pred("page_chunk_count", "truthy")),
        action="page_range",
    ),
    Rule(
        when=(
            Pred("has_query_ref", "truthy"),
            Pred("page_chunk_count", "truthy"),
            Pred("single_page", "truthy"),
        ),
        action="page_range",
    ),
    Rule(
        when=(Pred("has_query_ref", "truthy"), Pred("page_chunk_count", "truthy")),
        action="page_plus_vector",
    ),
    Rule(when=(), action="vector_only"),
)
_CONFIRMED_OUTCOME_RULES = (
    Rule(when=(Pred("correct", "eq", True),), action="correct"),
    Rule(when=(Pred("correct", "eq", False),), action="incorrect"),
    Rule(when=(), action="submitted"),
)
_LEARN_CHAT_STATUS_RULES = (
    Rule(when=(Pred("document_complete", "truthy"),), action="document_complete"),
    Rule(
        when=(Pred("page_complete", "truthy"), Pred("no_current", "truthy")),
        action="page_complete",
    ),
    Rule(
        when=(Pred("generation_pending", "truthy"), Pred("no_current", "truthy")),
        action="generation_pending",
    ),
    Rule(when=(Pred("has_assertion", "truthy"),), action="active_question"),
    Rule(when=(), action="idle"),
)
_CHAT_SURFACE_RULES = (
    Rule(when=(Pred("known", "truthy"),), action="keep"),
    Rule(when=(), action="general"),
)
_PIN_LEARN_QUEUE_RULES = (
    Rule(
        when=(Pred("mode", "in_set", ("read", "brainstorm", "socratic")),),
        action="skip",
    ),
    Rule(when=(), action="pin"),
)
_CHAT_GUARDRAIL_RULES = (
    Rule(when=(Pred("has_context", "falsey"),), action="skip"),
    Rule(when=(Pred("active_question", "falsey"),), action="skip"),
    Rule(when=(Pred("asks_answer", "truthy"),), action="skip"),
    Rule(when=(Pred("followup", "truthy"),), action="skip"),
    Rule(when=(), action="apply"),
)
_CHAT_ERROR_RULES = (
    Rule(when=(Pred("failover", "falsey"),), action="busy"),
    Rule(when=(Pred("rate", "truthy"),), action="rate"),
    Rule(when=(Pred("timeout", "truthy"),), action="timeout"),
    Rule(when=(), action="busy"),
)
_CHAT_SYSTEM_RULES = (
    Rule(when=(Pred("socratic", "truthy"),), action="socratic_system"),
    Rule(when=(Pred("brainstorm", "truthy"),), action="brainstorm_system"),
    Rule(when=(), action="tutor_system"),
)


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    return pick(p in _POLICY_ALIASES, lambda: DEFAULT_POLICY, lambda: p or DEFAULT_POLICY)


def is_conversational_followup(message: str) -> bool:
    """True for short acknowledgements / nudges / rephrase requests."""
    return bool(_NO_RETRIEVE_RE.match((message or "").strip()))


def decide_retrieval(
    message: str,
    *,
    scope: dict | None,
    has_history: bool = False,
    policy: str | None = None,
) -> RetrievalGateVerdict:
    """Sole retrieval-gate seam. Orchestration must not invent parallel skip rules."""
    pol = normalize_policy(policy)
    message = (message or "").strip()
    scope = scope or {}
    hit = first_match(
        _RETRIEVE_RULES,
        {
            "empty": not message,
            "brainstorm": str(scope.get("mode") or "").lower() == "brainstorm",
            "prefetched": scope.get("reference_source") in ("wikipedia", "dictionary"),
            "no_history": not has_history,
            "page_scope": scope.get("page_start") is not None or scope.get("page_end") is not None,
            "current_page": scope.get("current_page") is not None,
            "selection": bool(scope.get("selection_text")),
            "followup": is_conversational_followup(message),
        },
    )
    reason: GateReason = hit.action  # type: ignore[assignment]
    return RetrievalGateVerdict(bool(hit.extras["retrieve"]), reason, policy=pol)


@dataclass(frozen=True)
class CacheReuseVerdict:
    reuse: bool
    similarity: float
    threshold: float = RESPONSE_CACHE_SIMILARITY
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def decide_cache_reuse(
    similarity: float,
    *,
    threshold: float = RESPONSE_CACHE_SIMILARITY,
    policy: str | None = None,
) -> CacheReuseVerdict:
    """Whether a semantic chat-cache hit is close enough to reuse."""
    pol = normalize_policy(policy)
    sim = float(similarity)
    thr = float(threshold)
    return CacheReuseVerdict(
        reuse=sim >= thr,
        similarity=sim,
        threshold=thr,
        policy=pol,
    )


def grade_context_top_n(*, top_n: int = GRADE_CONTEXT_TOP_N) -> int:
    """How many ranked chunks to paste into MCQ grade-tutor prompts."""
    return max(1, int(top_n))


@dataclass(frozen=True)
class BrainstormSampleVerdict:
    texts: tuple[str, ...]
    sample_n: int
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def plan_brainstorm_sample(
    chunks: Sequence[str],
    *,
    sample_n: int = BRAINSTORM_CHUNK_SAMPLE,
    policy: str | None = None,
) -> BrainstormSampleVerdict:
    """Evenly spaced whole-source sample for brainstorm (not vector top-k)."""
    pol = normalize_policy(policy)
    n = max(1, int(sample_n))
    return pick(
        not chunks,
        lambda: BrainstormSampleVerdict(texts=(), sample_n=n, policy=pol),
        lambda: BrainstormSampleVerdict(
            texts=tuple(str(c) for c in chunks[:: max(1, len(chunks) // n)][:n]),
            sample_n=n,
            policy=pol,
        ),
    )


@dataclass(frozen=True)
class ChatHistoryVerdict:
    messages: tuple[dict[str, Any], ...]
    compressed: bool
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def compress_chat_history(
    prior: Sequence[dict[str, Any]],
    *,
    compress_above: int = CHAT_HISTORY_COMPRESS_ABOVE,
    keep_tail: int = CHAT_HISTORY_KEEP_TAIL,
    snippet_chars: int = CHAT_HISTORY_SNIPPET_CHARS,
    policy: str | None = None,
) -> ChatHistoryVerdict:
    """Keep last N messages + one compressed earlier block (cuts input tokens)."""
    pol = normalize_policy(policy)
    msgs = list(prior or [])
    above = max(0, int(compress_above))
    tail_n = max(1, int(keep_tail))
    snip = max(40, int(snippet_chars))

    def bit(m: dict[str, Any]) -> str | None:
        content = (m.get("content") or "").strip().replace("\n", " ")
        return pick(
            not content,
            lambda: None,
            lambda: f"{m.get('role') or '?'}: {content[:snip]}",
        )

    def compressed() -> ChatHistoryVerdict:
        tail = msgs[-tail_n:]
        bits = list(filter(None, map(bit, msgs[:-tail_n])))
        return pick(
            not bits,
            lambda: ChatHistoryVerdict(messages=tuple(tail), compressed=True, policy=pol),
            lambda: ChatHistoryVerdict(
                messages=tuple(
                    [
                        {
                            "role": "user",
                            "content": "Earlier in this thread (compressed):\n" + "\n".join(bits),
                        },
                        *tail,
                    ]
                ),
                compressed=True,
                policy=pol,
            ),
        )

    return pick(
        len(msgs) <= above,
        lambda: ChatHistoryVerdict(messages=tuple(msgs), compressed=False, policy=pol),
        compressed,
    )


def plan_coding_assist_history() -> int:
    """How many prior coding-assist turns to send with the next tutor call."""
    return CODING_ASSIST_HISTORY


def plan_chat_thread_history() -> int:
    """How many recent tutor-chat turns to keep in the LLM prefix."""
    return CHAT_THREAD_HISTORY


def plan_coding_assist_code_chars() -> int:
    """How much learner source the coding-assist tutor prompt may include."""
    return CODING_ASSIST_CODE_CHARS


@dataclass(frozen=True)
class ChatCacheEligibleVerdict:
    eligible: bool
    reason: str
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def evaluate_chat_cache_eligible(
    *,
    include_image: bool,
    selection_text: str | None,
    has_citations: bool,
    doc_count: int,
    has_history: bool,
    conversational_followup: bool = False,
    prefetched_reference: bool = False,
) -> ChatCacheEligibleVerdict:
    """Whether a semantic chat-cache lookup/store is safe for this turn."""
    hit = first_match(
        _CACHE_ELIGIBLE_RULES,
        {
            "include_image": include_image,
            "prefetched_reference": prefetched_reference,
            "selection_text": bool(selection_text),
            "has_citations": has_citations,
            "multi_doc": int(doc_count) != 1,
            "followup": has_history and conversational_followup,
        },
    )
    return ChatCacheEligibleVerdict(hit.action == "ok", hit.action)


def decide_page_pin(
    scope: dict | None,
    *,
    policy: str | None = None,
) -> PagePinVerdict:
    """Whether retrieval should try the active page before the RAG window.

    Orchestration must not invent parallel current_page fast-path if-else.
    """
    pol = normalize_policy(policy)
    scope = scope or {}
    raw = scope.get("current_page")

    def from_int(page: int) -> PagePinVerdict:
        return pick(
            page < 1,
            lambda: PagePinVerdict(pin_current_first=False, page=None, policy=pol),
            lambda: PagePinVerdict(pin_current_first=True, page=page, policy=pol),
        )

    def parsed() -> PagePinVerdict:
        try:
            return from_int(int(raw))
        except (TypeError, ValueError):
            return PagePinVerdict(pin_current_first=False, page=None, policy=pol)

    return pick(
        raw is None,
        lambda: PagePinVerdict(pin_current_first=False, page=None, policy=pol),
        parsed,
    )


def needs_retrieval(
    message: str,
    *,
    scope: dict | None,
    has_history: bool = False,
    policy: str | None = None,
) -> bool:
    """Compat bool wrapper around ``decide_retrieval``."""
    return decide_retrieval(
        message, scope=scope, has_history=has_history, policy=policy
    ).retrieve


def plan_rag_window(
    current_page: int,
    study_pages: list[int],
    *,
    max_pages: int = MAX_RAG_PAGES,
    policy: str | None = None,
) -> RagWindowPlan:
    """Pages to index for chat RAG around the learner's current page."""
    pol = normalize_policy(policy)
    max_pages = max(1, int(max_pages))

    def from_study() -> RagWindowPlan:
        study = sorted(set(map(int, filter(lambda p: int(p) >= 1, study_pages))))
        current = max(1, int(current_page))

        def snapped() -> int:
            future = list(filter(lambda p: p >= current, study))
            return pick(bool(future), lambda: future[0], lambda: study[-1])

        current = pick(current not in study, snapped, lambda: current)
        look_ahead = choose(current < 3, 1, 2)
        window_end = current + look_ahead
        candidates = list(filter(lambda p: p <= window_end, study))
        pages = pick(
            not candidates,
            lambda: (current,),
            lambda: pick(
                len(candidates) <= max_pages,
                lambda: tuple(candidates),
                lambda: tuple(candidates[-max_pages:]),
            ),
        )
        return RagWindowPlan(
            pages=pages,
            current_page=current,
            max_pages=max_pages,
            policy=pol,
        )

    return pick(
        not study_pages,
        lambda: RagWindowPlan(
            pages=(max(1, int(current_page)),),
            current_page=max(1, int(current_page)),
            max_pages=max_pages,
            policy=pol,
        ),
        from_study,
    )


def plan_learn_context_rag(
    current_page: int,
    study_pages: Sequence[int],
    *,
    newspaper: bool,
    max_pages: int = MAX_RAG_PAGES,
    policy: str | None = None,
) -> RagWindowPlan:
    """Learn-chat RAG: newspaper stays on the current page; else the usual window."""
    return pick(
        newspaper,
        lambda: RagWindowPlan(
            pages=(max(1, int(current_page)),),
            current_page=max(1, int(current_page)),
            max_pages=1,
            policy=normalize_policy(policy),
        ),
        lambda: plan_rag_window(
            current_page, list(study_pages), max_pages=max_pages, policy=policy
        ),
    )


def should_attach_learn_session_context(*, scope_mode: str | None) -> bool:
    """Read-mode chat is about the document, not the Learn loop."""
    return str(scope_mode or "").strip().lower() != "read"


def plan_queue_rag_pages(
    *,
    newspaper: bool,
    current_page: int,
    study_pages: Sequence[int],
    stored_window: Sequence[int] | None,
) -> tuple[int, ...]:
    """Newspaper recomputes from the current page; uploads keep the stored window."""
    return pick(
        newspaper,
        lambda: plan_rag_window(current_page, list(study_pages)).pages,
        lambda: pick(
            bool(stored_window),
            lambda: tuple(int(p) for p in stored_window),
            lambda: plan_rag_window(current_page, list(study_pages)).pages,
        ),
    )


def finish_ranked_chunks(
    query: str,
    chunks: list[dict],
    *,
    top_n: int = DEFAULT_TOP_N,
    rerank_enabled: bool = False,
    policy: str | None = None,
) -> ChunkRankVerdict:
    """Rank / truncate over-fetched chunks to top_n.

    When ``rerank_enabled``, delegates score order to the rerank service;
    otherwise keeps input order (cosine / page order).
    """
    pol = normalize_policy(policy)
    top_n = max(1, int(top_n))

    def reranked() -> ChunkRankVerdict:
        from app.services.rerank import rerank_chunks

        ranked = rerank_chunks(query, chunks, top_n=top_n)
        return ChunkRankVerdict(
            chunks=tuple(ranked),
            top_n=top_n,
            used_rerank=True,
            policy=pol,
        )

    return pick(
        len(chunks) <= top_n,
        lambda: ChunkRankVerdict(
            chunks=tuple(chunks),
            top_n=top_n,
            used_rerank=False,
            policy=pol,
        ),
        lambda: pick(
            not rerank_enabled,
            lambda: ChunkRankVerdict(
                chunks=tuple(chunks[:top_n]),
                top_n=top_n,
                used_rerank=False,
                policy=pol,
            ),
            reranked,
        ),
    )


RagReadyReason = Literal["missing_doc", "stale_window", "flag_ready", "empty", "all_indexed", "partial"]
ChunkStrategy = Literal["pin_page", "page_range", "page_plus_vector", "vector_only"]


@dataclass(frozen=True)
class RagWindowReadyVerdict:
    ready: bool
    target_pages: tuple[int, ...]
    reason: RagReadyReason
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


@dataclass(frozen=True)
class ChunkRetrievalPlan:
    strategy: ChunkStrategy
    page_start: int | None
    page_end: int | None
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def extract_query_page_range(query: str) -> tuple[int | None, int | None]:
    """Parse a learner page reference out of a chat query."""
    text = (query or "").strip()

    def from_match() -> tuple[int | None, int | None]:
        match = _PAGE_REF_RE.search(text)
        return pick(
            not match,
            lambda: (None, None),
            lambda: _range_from_groups(list(filter(lambda g: g is not None, match.groups()))),
        )

    def _range_from_groups(groups: list[str]) -> tuple[int | None, int | None]:
        start = int(groups[0])
        end = pick(
            len(groups) > 1 and bool(groups[1]),
            lambda: int(groups[1]),
            lambda: start,
        )
        return pick(
            start <= 0 or end <= 0,
            lambda: (None, None),
            lambda: (min(start, end), max(start, end)),
        )

    return pick(not text, lambda: (None, None), from_match)


def evaluate_rag_window_ready(
    *,
    has_doc: bool,
    current_page: int,
    saved_window: Sequence[int],
    flag_ready: bool,
    study_pages: Sequence[int],
    ready_pages: set[int],
    policy: str | None = None,
) -> RagWindowReadyVerdict:
    """Whether the learner's current RAG window is indexed enough to proceed.

    When the current page has walked past the persisted window, re-plan instead
    of trusting the sticky ready flag (that deadlock sat at Planning the quiz).
    """
    pol = normalize_policy(policy)

    def finish(target: tuple[int, ...], reason: RagReadyReason) -> RagWindowReadyVerdict:
        return pick(
            not target,
            lambda: RagWindowReadyVerdict(True, (), "empty", policy=pol),
            lambda: pick(
                all(p in ready_pages for p in target),
                lambda: RagWindowReadyVerdict(True, target, "all_indexed", policy=pol),
                lambda: RagWindowReadyVerdict(False, target, reason, policy=pol),
            ),
        )

    def after_doc() -> RagWindowReadyVerdict:
        saved = tuple(int(p) for p in saved_window)
        current = int(current_page or 0)
        stale = current > 0 and current not in set(saved)
        return pick(
            stale,
            lambda: finish(
                plan_rag_window(current, list(study_pages), policy=pol).pages,
                "stale_window",
            ),
            lambda: pick(
                flag_ready,
                lambda: RagWindowReadyVerdict(True, saved, "flag_ready", policy=pol),
                lambda: finish(saved, choose(not saved, "empty", "partial")),
            ),
        )

    return pick(
        not has_doc,
        lambda: RagWindowReadyVerdict(False, (), "missing_doc", policy=pol),
        after_doc,
    )


def plan_chunk_retrieval(
    pin: PagePinVerdict,
    scope: dict | None,
    query: str,
    *,
    pin_missed: bool = False,
    page_chunk_count: int = 0,
    page_chunks_fetched: bool = False,
    policy: str | None = None,
) -> ChunkRetrievalPlan:
    """How to fetch tutor chunks. Orchestration runs embed/search/merge."""
    pol = normalize_policy(policy)
    scope = scope or {}
    query_start, query_end = extract_query_page_range(query)
    scope_start = scope.get("page_start")
    scope_end = scope.get("page_end")
    has_scope = scope_start is not None
    has_query_ref = query_start is not None
    current_page = pin.page
    page_start = pick(
        has_query_ref,
        lambda: query_start,
        lambda: pick(has_scope, lambda: int(scope_start), lambda: None),
    )
    page_end = pick(
        has_query_ref,
        lambda: query_end,
        lambda: pick(has_scope, lambda: int(scope_end), lambda: None),
    )
    page_start, page_end = pick(
        current_page is not None and page_start is None,
        lambda: (int(current_page), int(current_page)),
        lambda: (
            page_start,
            pick(page_start is not None and page_end is None, lambda: page_start, lambda: page_end),
        ),
    )
    pinned_single_page = (
        page_start is not None
        and page_end is not None
        and page_start == page_end
        and (has_scope or current_page is not None or has_query_ref)
    )
    hit = first_match(
        _CHUNK_STRATEGY_RULES,
        {
            "pin_now": pin.pin_current_first and pin.page is not None and not pin_missed,
            "need_page_fetch": not page_chunks_fetched and page_start is not None,
            "pinned_single": pinned_single_page,
            "page_chunk_count": bool(page_chunk_count),
            "has_query_ref": has_query_ref,
            "single_page": page_start == page_end,
        },
    )
    start = pick(bool(hit.extras.get("use_pin")), lambda: pin.page, lambda: page_start)
    end = pick(bool(hit.extras.get("use_pin")), lambda: pin.page, lambda: page_end)
    strategy: ChunkStrategy = hit.action  # type: ignore[assignment]
    return ChunkRetrievalPlan(
        strategy=strategy,
        page_start=start,
        page_end=end,
        policy=pol,
    )


WEAK_CONCEPT_TOP_N = 3


@dataclass(frozen=True)
class WeakConceptPlan:
    concepts: tuple[tuple[str, float], ...]
    policy: str = DEFAULT_POLICY
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def plan_tutor_weak_concepts(
    concept_ability: Mapping[str, Any] | None,
    *,
    top_n: int = WEAK_CONCEPT_TOP_N,
) -> WeakConceptPlan:
    """Lowest calibrated abilities the tutor should prefer in hints."""
    rows = [
        (str(key), float(value))
        for key, value in filter(
            lambda kv: kv[1] is not None,
            (concept_ability or {}).items(),
        )
    ]
    rows.sort(key=lambda kv: kv[1])
    cap = max(1, int(top_n))
    return WeakConceptPlan(tuple(rows[:cap]))


def plan_unconfirmed_tutor_policy(
    *,
    has_mcq_options: bool,
    confirmed_choice_index: int | None,
) -> str | None:
    """Hint-only while the learner has options but has not checked an answer."""
    return pick(
        not has_mcq_options or confirmed_choice_index is not None,
        lambda: None,
        lambda: (
            "- Tutor policy: explain concepts and give hints only; do not reveal "
            "which option is correct unless the learner explicitly asks for the answer."
        ),
    )


@dataclass(frozen=True)
class RagChunkSplitPlan:
    max_chars: int
    overlap: int
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def plan_rag_chunk_split(
    *,
    max_chars: int | None = None,
    overlap: int | None = None,
) -> RagChunkSplitPlan:
    """How large indexed RAG chunks are, and how much consecutive pieces overlap."""
    return RagChunkSplitPlan(
        pick(max_chars is not None, lambda: int(max_chars), lambda: RAG_CHUNK_MAX_CHARS),
        pick(overlap is not None, lambda: int(overlap), lambda: RAG_CHUNK_OVERLAP),
    )


@dataclass(frozen=True)
class CookAspectHintPlan:
    hit_limit: int
    snippet_chars: int
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def plan_cook_aspect_hint_context() -> CookAspectHintPlan:
    """How many retrieval hits and how much of each snippet to paste into cook."""
    return CookAspectHintPlan(COOK_ASPECT_HINT_HITS, COOK_ASPECT_HINT_SNIPPET_CHARS)


@dataclass(frozen=True)
class TopicExplainContextPlan:
    chunk_limit: int
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def plan_topic_explain_context() -> TopicExplainContextPlan:
    """How many document chunks ground a topic explanation."""
    return TopicExplainContextPlan(TOPIC_EXPLAIN_CHUNK_LIMIT)


LearnChatStatus = Literal[
    "document_complete",
    "page_complete",
    "generation_pending",
    "active_question",
    "idle",
]


def label_confirmed_answer_outcome(answer_correct: bool | None) -> str:
    """Tutor copy for a checked Learn answer: correct, incorrect, or submitted."""
    return first_match(_CONFIRMED_OUTCOME_RULES, {"correct": answer_correct}).action


def evaluate_learn_chat_status(
    *,
    document_complete: bool,
    page_complete: bool,
    generation_pending: bool,
    current_assertion_id: Any,
    assertion_id: str | None,
) -> LearnChatStatus:
    """Which Learn-session block the tutor context should emit next."""
    hit = first_match(
        _LEARN_CHAT_STATUS_RULES,
        {
            "document_complete": bool(document_complete),
            "page_complete": bool(page_complete),
            "generation_pending": bool(generation_pending),
            "no_current": not current_assertion_id,
            "has_assertion": bool(current_assertion_id or assertion_id),
        },
    )
    status: LearnChatStatus = hit.action  # type: ignore[assignment]
    return status


def looks_like_quiz(message: str) -> bool:
    """Whether this turn asked for quiz / MCQ / exam questions."""
    return bool(_QUIZ_RE.search(message or ""))


def user_requests_mcq_answer(message: str) -> bool:
    """Whether the learner explicitly asked which option is correct."""
    return bool(_MCQ_ANSWER_REQUEST_RE.search(message or ""))


def learn_context_has_active_question(learn_context: str | None) -> bool:
    """Learn-session block currently names a live MCQ stem."""
    return bool(learn_context and "Current question stem:" in learn_context)


def is_prefetched_reference(scope: Mapping[str, Any] | None) -> bool:
    """Wikipedia / dictionary lookup already embedded in the turn."""
    return (scope or {}).get("reference_source") in ("wikipedia", "dictionary")


def message_has_prefetched_reference(message: str) -> bool:
    """User message already carries a Wikipedia summary prefix."""
    return bool(_WIKIPEDIA_PREFETCH_RE.search(message or ""))


def plan_retrieval_query(message: str) -> str:
    """Prefer a quoted highlight over the full turn text for vector search."""
    match = _HIGHLIGHTED_RE.search(message or "")
    text = (message or "").strip()
    return pick(
        bool(match),
        lambda: match.group(1).strip(),
        lambda: pick(len(text) > 500, lambda: text[:500], lambda: text),
    )


def plan_chat_surface(mode: str | None) -> str:
    """Read / Learn / Test / Brainstorm / Socratic stay on separate threads."""
    surface = (mode or "").strip().lower()
    hit = first_match(_CHAT_SURFACE_RULES, {"known": surface in _CHAT_SURFACES})
    return pick(hit.action == "keep", lambda: surface, lambda: "general")


def should_pin_learn_queue(*, scope_mode: str | None) -> bool:
    """Skip Learn-queue pinning for read, brainstorm, and socratic surfaces."""
    mode = str(scope_mode or "").strip().lower()
    return first_match(_PIN_LEARN_QUEUE_RULES, {"mode": mode}).action == "pin"


def evaluate_chat_guardrail(
    *,
    learn_context: str | None,
    message: str,
    has_history: bool,
) -> bool:
    """Inject the no-answer-letter trailer unless the learner asked for it."""
    hit = first_match(
        _CHAT_GUARDRAIL_RULES,
        {
            "has_context": bool(learn_context),
            "active_question": learn_context_has_active_question(learn_context),
            "asks_answer": user_requests_mcq_answer(message),
            "followup": has_history and is_conversational_followup(message),
        },
    )
    return hit.action == "apply"


@dataclass(frozen=True)
class ChatTrailerPlan:
    prefetched_directive: bool
    mcq_guardrail: bool
    quiz_format: bool
    policy_version: str = TUTOR_RETRIEVAL_VERSION


def plan_chat_trailers(
    *,
    prefetched_reference: bool,
    learn_context: str | None,
    message: str,
    has_history: bool,
) -> ChatTrailerPlan:
    """Which per-turn trailers to append after the user message."""
    return ChatTrailerPlan(
        prefetched_directive=bool(prefetched_reference),
        mcq_guardrail=evaluate_chat_guardrail(
            learn_context=learn_context,
            message=message,
            has_history=has_history,
        ),
        quiz_format=looks_like_quiz(message),
    )


def plan_chat_system_prompt_key(*, socratic: bool, brainstorm: bool) -> str:
    """Stable system-prompt key for the current study surface."""
    return first_match(
        _CHAT_SYSTEM_RULES,
        {"socratic": bool(socratic), "brainstorm": bool(brainstorm)},
    ).action


def plan_chat_user_error(exc: BaseException, *, failover_eligible: bool) -> str:
    """User-safe chat stream error copy (no internal exception text)."""
    message = str(exc).lower()
    hit = first_match(
        _CHAT_ERROR_RULES,
        {
            "failover": failover_eligible,
            "rate": "rate" in message or "429" in message or "too many requests" in message,
            "timeout": "timeout" in message or "timed out" in message,
        },
    )
    return _CHAT_ERROR_COPY[hit.action]
