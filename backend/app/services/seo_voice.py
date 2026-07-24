"""Voice post-process — Steve Jobs clarity; strip AI tells."""

from __future__ import annotations

import re

# Em-dash and common stand-ins → plain hyphen or comma-friendly split.
_EM_DASH_RE = re.compile(r"\s*[—–―‒]\s*")
_DOUBLE_HYPHEN_RE = re.compile(r"\s+--\s+")

_BANNED_PHRASES = (
    r"\bdelve(?:s|d|ing)?(?:\s+into)?\b",
    r"\bin\s+today'?s\s+(?:digital\s+)?landscape\b",
    r"\blandscape\b",
    r"\brobust(?:ness)?\b",
    r"\bleverage(?:s|d|ing)?\b",
    r"\bcutting[- ]edge\b",
    r"\bgame[- ]changer\b",
    r"\bunlock(?:s|ed|ing)?\s+(?:the\s+)?(?:power|potential)\b",
    r"\bas\s+an\s+ai\b",
    r"\bi'?m\s+an?\s+ai\b",
    r"\bit'?s\s+important\s+to\s+note\s+that\b",
    r"\bin\s+conclusion,?\b",
    r"\blet'?s\s+dive\s+in\b",
    r"\btapestry\b",
    r"\bmyriad\b",
    r"\bparamount\b",
)

_BANNED_RE = re.compile("|".join(_BANNED_PHRASES), re.IGNORECASE)


def strip_em_dashes(text: str) -> str:
    """Replace em/en dashes and ` -- ` stand-ins with a simple hyphen."""
    cleaned = _EM_DASH_RE.sub(" - ", text or "")
    cleaned = _DOUBLE_HYPHEN_RE.sub(" - ", cleaned)
    return re.sub(r" {2,}", " ", cleaned).strip()


def strip_banned_ai_phrases(text: str) -> str:
    """Remove known AI-sludge phrases; leave surrounding sentence structure."""
    cleaned = _BANNED_RE.sub("", text or "")
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def humanize_voice(text: str) -> str:
    """Full post-process pass for published body/title/lede."""
    return strip_banned_ai_phrases(strip_em_dashes(text or ""))


def humanize_fields(*, title: str, lede: str, body_md: str) -> dict[str, str]:
    return {
        "title": humanize_voice(title),
        "lede": humanize_voice(lede),
        "body_md": humanize_voice(body_md),
    }
