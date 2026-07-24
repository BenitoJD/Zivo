"""Usefulness triage — skip internal-only / not-useful-to-strangers material."""

from __future__ import annotations

import re

_INTERNAL_MARKERS = re.compile(
    r"\b("
    r"confidential|internal\s+only|do\s+not\s+(?:share|distribute|forward)|"
    r"proprietary|for\s+(?:internal|employee)\s+use|"
    r"meeting\s+notes?|standup\s+notes?|1\s*:\s*1\s+notes?|"
    r"action\s+items?\s+for\s+(?:me|us|team)|"
    r"password|api[_ ]?key|secret[_ ]?key|private\s+key|"
    r"ssn|salary|compensation\s+band|"
    r"todo:\s*|fixme:\s*|wip\b|"
    r"dear\s+team,|hi\s+team,"
    r")\b",
    re.I,
)

_PERSONAL_MARKERS = re.compile(
    r"\b("
    r"my\s+(?:boss|manager|spouse|wife|husband|kids?|landlord)|"
    r"remind\s+me\s+to|don'?t\s+forget\s+(?:to\s+)?(?:pick\s+up|call)|"
    r"grocery\s+list|personal\s+diary|journal\s+entry"
    r")\b",
    re.I,
)

_USEFUL_SIGNALS = re.compile(
    r"\b("
    r"how\s+(?:to|does|do)|why\s+(?:does|do|is)|what\s+is|"
    r"explain|concept|principle|theorem|algorithm|"
    r"system\s+design|architecture|database|cache|queue|"
    r"constitution|parliament|economy|inflation|policy|"
    r"interview|exam|upsc|practice|trade[- ]?off"
    r")\b",
    re.I,
)


def triage_usefulness(text: str, *, filename: str = "") -> tuple[bool, str]:
    """Return (useful, reason). Heuristic gate before LLM rewrite."""
    body = (text or "").strip()
    name = (filename or "").strip().lower()
    if len(body) < 400:
        return False, "too_short"
    if _INTERNAL_MARKERS.search(body) or _INTERNAL_MARKERS.search(name):
        return False, "internal_markers"
    if _PERSONAL_MARKERS.search(body):
        return False, "personal_markers"
    # Strong internal filenames
    if any(
        tok in name
        for tok in ("password", "secrets", "1on1", "1-1", "standup", "payroll")
    ):
        return False, "internal_filename"
    if not _USEFUL_SIGNALS.search(body):
        # Still allow long editorial/explanatory prose without keyword hits
        if len(body) < 1200:
            return False, "no_useful_signal"
    return True, "ok"
