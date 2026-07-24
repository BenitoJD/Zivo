"""Drop ads / junk newspaper pages before MCQ generation.

Heuristic first (cheap, deterministic). Optional LLM refine later behind config.
"""

from __future__ import annotations

import re

_AD_MARKERS = re.compile(
    r"\b("
    r"advertisement|classifieds?|matrimonial|tenders?|"
    r"subscribe\s+now|scan\s+qr|whatsapp\s+us|"
    r"limited\s+period\s+offer|buy\s+now|call\s+toll\s*free|"
    r"horoscope|crossword|sudoku|weather\s+report"
    r")\b",
    re.I,
)

_MASTHEAD_MARKERS = re.compile(
    r"\b(volume\s+\d+|regd\.?\s*no\.?|postal\s+regn|rni\s+no)\b",
    re.I,
)


def classify_page_text(page_text: str) -> tuple[str, str]:
    """Return (label, rationale). label in editorial|ad|masthead|low_signal."""
    text = (page_text or "").strip()
    if len(text) < 120:
        return "low_signal", "Too little extractable text for study."
    ad_hits = len(_AD_MARKERS.findall(text))
    if ad_hits >= 2 or (ad_hits >= 1 and len(text) < 800):
        return "ad", f"Ad/classified markers ({ad_hits})."
    if _MASTHEAD_MARKERS.search(text) and len(text) < 600:
        return "masthead", "Looks like masthead / registration boilerplate."
    # Dense price/phone patterns without prose → ad-ish
    phones = len(re.findall(r"\b\d{5,}[-/\s]?\d{4,}\b", text))
    if phones >= 4 and len(text) < 1500:
        return "ad", "Many phone/price-like tokens; treat as ad page."
    return "editorial", "Usable editorial signal."


def is_editorial(page_text: str) -> bool:
    label, _ = classify_page_text(page_text)
    return label == "editorial"
