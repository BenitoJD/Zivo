"""Adaptive retrieval gate — decide whether a chat turn needs new retrieval.

Many turns are continuations, acknowledgements, or follow-ups that need no new
chunks ("thanks", "go on", "explain that again"). Skipping retrieval for them
avoids a query embed + pgvector search and ~1,600 tokens of context per turn.
Heuristic-only (regex/length) for now: cheap, deterministic, unit-testable.
"""

from __future__ import annotations

import re

# A scope that pins a page range or carries a highlight is itself a strong
# signal the user wants document content — always retrieve when one is set.
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


def needs_retrieval(
    message: str,
    *,
    scope: dict | None,
    has_history: bool = False,
) -> bool:
    """Return True if this turn should run vector/page retrieval.

    Always retrieve when:
      - there is no conversation history yet (first turn needs context)
      - a page scope, page_end, or selection_text is set (explicit doc focus)
      - the message doesn't match the no-retrieve continuation pattern

    Returns False only for short follow-ups in an ongoing conversation with no
    document scope pinned.
    """
    message = (message or "").strip()
    if not message:
        return False
    if not has_history:
        return True
    scope = scope or {}
    if scope.get("page_start") is not None or scope.get("page_end") is not None:
        return True
    if scope.get("selection_text"):
        return True
    return not _NO_RETRIEVE_RE.match(message)
