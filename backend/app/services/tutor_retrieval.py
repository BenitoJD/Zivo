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
from typing import Any, Literal, Sequence

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
# Chat history: compress older turns once the thread exceeds this length.
CHAT_HISTORY_COMPRESS_ABOVE = 4
CHAT_HISTORY_KEEP_TAIL = 2
CHAT_HISTORY_SNIPPET_CHARS = 180

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
