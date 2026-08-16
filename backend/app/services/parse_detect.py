"""Parse Detect Engine — format / magic-bytes / sparse probe.

Version: qb.parse_detect.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.engine_runtime import Pred, Rule, first_match

PARSE_DETECT_VERSION = "qb.parse_detect.v1"
DEFAULT_PARSE_DETECT_POLICY = "parse_detect_v1"
ParseFormat = Literal["pdf", "docx", "pptx", "html", "text", "image", "unknown"]

_RULES = (
    Rule(when=(Pred("head", "startswith", b"%PDF"),), action="pdf"),
    Rule(when=(Pred("head", "startswith", b"PK"), Pred("name", "endswith", ".docx")), action="docx"),
    Rule(when=(Pred("head", "startswith", b"PK"), Pred("name", "endswith", ".pptx")), action="pptx"),
    Rule(when=(Pred("head", "startswith", b"PK"),), action="docx"),
    Rule(when=(Pred("name", "endswith", ".html"),), action="html"),
    Rule(when=(Pred("name", "endswith", ".htm"),), action="html"),
    Rule(when=(Pred("name", "endswith", ".txt"),), action="text"),
    Rule(when=(Pred("name", "endswith", ".md"),), action="text"),
    Rule(when=(Pred("name", "endswith", ".png"),), action="image"),
    Rule(when=(Pred("name", "endswith", ".jpg"),), action="image"),
    Rule(when=(Pred("name", "endswith", ".jpeg"),), action="image"),
    Rule(when=(Pred("name", "endswith", ".webp"),), action="image"),
    Rule(when=(), action="unknown"),
)


@dataclass(frozen=True)
class ParseDetectVerdict:
    action: ParseFormat
    policy: str = DEFAULT_PARSE_DETECT_POLICY
    policy_version: str = PARSE_DETECT_VERSION


def evaluate_parse_detect(
    *,
    filename: str = "",
    header: bytes = b"",
    policy: str | None = None,
) -> ParseDetectVerdict:
    signals = {"head": header or b"", "name": (filename or "").lower()}
    hit = first_match(_RULES, signals)
    return ParseDetectVerdict(action=hit.action, policy=policy or DEFAULT_PARSE_DETECT_POLICY)
