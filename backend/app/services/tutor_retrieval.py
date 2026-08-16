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


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    if p in ("default", "tutor", "retrieval"):
        return DEFAULT_POLICY
    return p or DEFAULT_POLICY


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
    if not message:
        return RetrievalGateVerdict(False, "empty", policy=pol)
    scope = scope or {}
    if str(scope.get("mode") or "").lower() == "brainstorm":
        # Brainstorm uses kept-ideas context, not vector RAG.
        return RetrievalGateVerdict(False, "brainstorm", policy=pol)
    if scope.get("reference_source") in ("wikipedia", "dictionary"):
        # Client already fetched the reference text — skip vector RAG on this turn.
        return RetrievalGateVerdict(False, "prefetched_reference", policy=pol)
    if not has_history:
        return RetrievalGateVerdict(True, "first_turn", policy=pol)
    if scope.get("page_start") is not None or scope.get("page_end") is not None:
        return RetrievalGateVerdict(True, "page_scope", policy=pol)
    if scope.get("current_page") is not None:
        return RetrievalGateVerdict(True, "current_page", policy=pol)
    if scope.get("selection_text"):
        return RetrievalGateVerdict(True, "selection", policy=pol)
    if is_conversational_followup(message):
        return RetrievalGateVerdict(False, "continuation", policy=pol)
    return RetrievalGateVerdict(True, "content_query", policy=pol)


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
    if not chunks:
        return BrainstormSampleVerdict(texts=(), sample_n=n, policy=pol)
    step = max(1, len(chunks) // n)
    sample = tuple(str(c) for c in chunks[::step][:n])
    return BrainstormSampleVerdict(texts=sample, sample_n=n, policy=pol)


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
    if len(msgs) <= above:
        return ChatHistoryVerdict(messages=tuple(msgs), compressed=False, policy=pol)
    tail = msgs[-tail_n:]
    bits: list[str] = []
    for m in msgs[:-tail_n]:
        content = (m.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        role = m.get("role") or "?"
        bits.append(f"{role}: {content[:snip]}")
    if not bits:
        return ChatHistoryVerdict(messages=tuple(tail), compressed=True, policy=pol)
    compressed = [
        {
            "role": "user",
            "content": "Earlier in this thread (compressed):\n" + "\n".join(bits),
        },
        *tail,
    ]
    return ChatHistoryVerdict(messages=tuple(compressed), compressed=True, policy=pol)


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
    if include_image:
        return ChatCacheEligibleVerdict(False, "image")
    if prefetched_reference:
        return ChatCacheEligibleVerdict(False, "prefetched_reference")
    if selection_text:
        return ChatCacheEligibleVerdict(False, "selection")
    if not has_citations:
        return ChatCacheEligibleVerdict(False, "no_citations")
    if int(doc_count) != 1:
        return ChatCacheEligibleVerdict(False, "multi_doc")
    if has_history and conversational_followup:
        return ChatCacheEligibleVerdict(False, "conversational_followup")
    return ChatCacheEligibleVerdict(True, "ok")


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
    if raw is None:
        return PagePinVerdict(pin_current_first=False, page=None, policy=pol)
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return PagePinVerdict(pin_current_first=False, page=None, policy=pol)
    if page < 1:
        return PagePinVerdict(pin_current_first=False, page=None, policy=pol)
    return PagePinVerdict(pin_current_first=True, page=page, policy=pol)


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
    if not study_pages:
        pages = (max(1, int(current_page)),)
        return RagWindowPlan(
            pages=pages,
            current_page=pages[0],
            max_pages=max_pages,
            policy=pol,
        )

    study = sorted({int(p) for p in study_pages if int(p) >= 1})
    current = max(1, int(current_page))
    if current not in study:
        future = [p for p in study if p >= current]
        current = future[0] if future else study[-1]

    look_ahead = 1 if current < 3 else 2
    window_end = current + look_ahead
    candidates = [p for p in study if p <= window_end]
    if not candidates:
        pages = (current,)
    elif len(candidates) <= max_pages:
        pages = tuple(candidates)
    else:
        pages = tuple(candidates[-max_pages:])
    return RagWindowPlan(
        pages=pages,
        current_page=current,
        max_pages=max_pages,
        policy=pol,
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
    if newspaper:
        page = max(1, int(current_page))
        pol = normalize_policy(policy)
        return RagWindowPlan(
            pages=(page,),
            current_page=page,
            max_pages=1,
            policy=pol,
        )
    return plan_rag_window(
        current_page, list(study_pages), max_pages=max_pages, policy=policy
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
    if newspaper:
        return plan_rag_window(current_page, list(study_pages)).pages
    if stored_window:
        return tuple(int(p) for p in stored_window)
    return plan_rag_window(current_page, list(study_pages)).pages


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
    if len(chunks) <= top_n:
        return ChunkRankVerdict(
            chunks=tuple(chunks),
            top_n=top_n,
            used_rerank=False,
            policy=pol,
        )
    if not rerank_enabled:
        return ChunkRankVerdict(
            chunks=tuple(chunks[:top_n]),
            top_n=top_n,
            used_rerank=False,
            policy=pol,
        )
    from app.services.rerank import rerank_chunks

    ranked = rerank_chunks(query, chunks, top_n=top_n)
    return ChunkRankVerdict(
        chunks=tuple(ranked),
        top_n=top_n,
        used_rerank=True,
        policy=pol,
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
    if not text:
        return None, None
    match = _PAGE_REF_RE.search(text)
    if not match:
        return None, None
    groups = [g for g in match.groups() if g is not None]
    start = int(groups[0])
    end = int(groups[1]) if len(groups) > 1 and groups[1] else start
    if start <= 0 or end <= 0:
        return None, None
    return min(start, end), max(start, end)


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
    if not has_doc:
        return RagWindowReadyVerdict(False, (), "missing_doc", policy=pol)
    target = tuple(int(p) for p in saved_window)
    current = int(current_page or 0)
    if current > 0 and current not in set(target):
        planned = plan_rag_window(current, list(study_pages), policy=pol)
        target = planned.pages
        reason: RagReadyReason = "stale_window"
    elif flag_ready:
        return RagWindowReadyVerdict(True, target, "flag_ready", policy=pol)
    else:
        reason = "empty" if not target else "partial"
    if not target:
        return RagWindowReadyVerdict(True, (), "empty", policy=pol)
    ready = all(p in ready_pages for p in target)
    if ready:
        reason = "all_indexed"
    return RagWindowReadyVerdict(ready, target, reason, policy=pol)


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
    if pin.pin_current_first and pin.page is not None and not pin_missed:
        return ChunkRetrievalPlan(
            strategy="pin_page",
            page_start=pin.page,
            page_end=pin.page,
            policy=pol,
        )

    query_start, query_end = extract_query_page_range(query)
    scope_start = scope.get("page_start")
    scope_end = scope.get("page_end")
    has_scope = scope_start is not None
    has_query_ref = query_start is not None
    current_page = pin.page

    page_start = query_start if has_query_ref else (int(scope_start) if has_scope else None)
    page_end = query_end if has_query_ref else (int(scope_end) if has_scope else None)
    if current_page is not None and page_start is None:
        page_start = int(current_page)
        page_end = page_start
    if page_start is not None and page_end is None:
        page_end = page_start

    pinned_single_page = (
        page_start is not None
        and page_end is not None
        and page_start == page_end
        and (has_scope or current_page is not None or has_query_ref)
    )

    if not page_chunks_fetched and page_start is not None:
        return ChunkRetrievalPlan(
            strategy="page_range",
            page_start=page_start,
            page_end=page_end,
            policy=pol,
        )

    if pinned_single_page and page_chunk_count:
        return ChunkRetrievalPlan(
            strategy="page_range",
            page_start=page_start,
            page_end=page_end,
            policy=pol,
        )
    if has_query_ref and page_chunk_count and page_start == page_end:
        return ChunkRetrievalPlan(
            strategy="page_range",
            page_start=page_start,
            page_end=page_end,
            policy=pol,
        )
    if has_query_ref and page_chunk_count:
        return ChunkRetrievalPlan(
            strategy="page_plus_vector",
            page_start=page_start,
            page_end=page_end,
            policy=pol,
        )
    return ChunkRetrievalPlan(
        strategy="vector_only",
        page_start=page_start,
        page_end=page_end,
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
    rows: list[tuple[str, float]] = []
    for key, value in (concept_ability or {}).items():
        if value is None:
            continue
        rows.append((str(key), float(value)))
    rows.sort(key=lambda kv: kv[1])
    cap = max(1, int(top_n))
    return WeakConceptPlan(tuple(rows[:cap]))


def plan_unconfirmed_tutor_policy(
    *,
    has_mcq_options: bool,
    confirmed_choice_index: int | None,
) -> str | None:
    """Hint-only while the learner has options but has not checked an answer."""
    if not has_mcq_options or confirmed_choice_index is not None:
        return None
    return (
        "- Tutor policy: explain concepts and give hints only; do not reveal "
        "which option is correct unless the learner explicitly asks for the answer."
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
        int(max_chars) if max_chars is not None else RAG_CHUNK_MAX_CHARS,
        int(overlap) if overlap is not None else RAG_CHUNK_OVERLAP,
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
