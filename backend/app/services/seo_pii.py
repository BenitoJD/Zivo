"""PII scrub before SEO rewrite — names/emails/phones/IDs → fillers."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
# Indian + intl mobiles: +91 98765 43210, 9876543210, (022) 1234 5678
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s\-]*)?(?:\(?\d{2,5}\)?[\s\-]*)?\d{3,5}[\s\-]?\d{3,5}(?!\w)"
)
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_URL_WITH_USER_RE = re.compile(
    r"https?://[^\s]+(?:token|key|session|auth)=[^\s]+", re.I
)

# "Dear Ramesh," / "Hi Priya " style — first name after greeting.
_GREETING_NAME_RE = re.compile(
    r"\b((?:Dear|Hi|Hello|Hey)\s+)([A-Z][a-z]{1,20})(\b)",
)

# Standalone email-like local parts already covered; also scrub "my name is X".
_MY_NAME_RE = re.compile(
    r"\b((?:my|his|her|their)\s+name\s+is\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    re.I,
)

_STREET_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Nagar|Colony)\b",
    re.I,
)


def scrub_pii(text: str) -> str:
    """Replace PII with fillers. Deterministic; safe for public rewrite input."""
    out = text or ""
    out = _EMAIL_RE.sub("[email]", out)
    out = _URL_WITH_USER_RE.sub("[link]", out)
    out = _AADHAAR_RE.sub("[id]", out)
    out = _PAN_RE.sub("[id]", out)
    out = _PHONE_RE.sub("[phone]", out)
    out = _STREET_RE.sub("[address]", out)
    out = _GREETING_NAME_RE.sub(r"\1[name]\3", out)
    out = _MY_NAME_RE.sub(r"\1[name]", out)
    return out
