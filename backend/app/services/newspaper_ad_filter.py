"""Drop ads / junk / off-syllabus newspaper pages before MCQ generation.

Heuristic first (cheap, deterministic). Exam gate targets UPSC CSE Prelims GS
+ overlapping State PSC Group-1 / central Group-A themes.
"""

from __future__ import annotations

import re
from typing import Final

# --- Ad / junk markers (strengthen skip so these never cook) -----------------

_AD_MARKERS = re.compile(
    r"\b("
    r"advertisement|classifieds?|matrimonial|tenders?|"
    r"subscribe\s+now|scan\s+qr|whatsapp\s+us|"
    r"limited\s+period\s+offer|buy\s+now|call\s+toll\s*free|"
    r"horoscope|crossword|sudoku|weather\s+report|"
    r"flat\s+for\s+sale|sq\.?\s*ft|emi\s+starts|walk[- ]in\s+interview|"
    r"job\s+vacanc(?:y|ies)|situations?\s+vacant|"
    r"showtimes?|box\s+office|now\s+showing|"
    r"lottery|jackpot|astrology|"
    r"buy\s+call|sell\s+call|target\s+price|"
    r"prime\s+time|tv\s+guide|channel\s+listing"
    r")\b",
    re.I,
)

_MASTHEAD_MARKERS = re.compile(
    r"\b(volume\s+\d+|regd\.?\s*no\.?|postal\s+regn|rni\s+no)\b",
    re.I,
)

# Soft entertainment / pure sports noise — used with exam-score contrast.
_OFF_SYLLABUS_MARKERS = re.compile(
    r"\b("
    r"bollywood|hollywoodwood|hollywoodwood|box[- ]office|film\s+review|"
    r"celebrity|gossip|fashion\s+week|red\s+carpet|"
    r"recipe|cooking\s+tips|lifestyle|"
    r"ipl\s+match|scorecard|wickets?|overs?\s+\d+|run\s+rate|"
    r"football\s+score|tennis\s+open|grand\s+slam|"
    r"reality\s+show|soap\s+opera"
    r")\b",
    re.I,
)

# --- Compact UPSC / Group-1 / Group-A theme allowlist -----------------------
# Prelims GS Paper I + shared Group-1/A core (polity, economy, geo, env,
# history, IR/security, science-policy, social sector, disaster). Not a dump.

_THEME_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "polity": (
        "constitution",
        "parliament",
        "lok sabha",
        "rajya sabha",
        "supreme court",
        "high court",
        "fundamental rights",
        "directive principles",
        "panchayati",
        "election commission",
        "federalism",
        "ordinance",
        "constitutional amendment",
        "governor",
        "president of india",
        "cabinet",
        "judiciary",
        "public policy",
        "bill passed",
        "article 370",
        "article 356",
        "speaker",
        "cag",
        "digital public infrastructure",
    ),
    "economy": (
        "gdp",
        "inflation",
        "reserve bank",
        "rbi",
        "central bank",
        "monetary policy",
        "fiscal deficit",
        "union budget",
        "gst",
        "niti aayog",
        "repo rate",
        "basis points",
        "bond yield",
        "msme",
        "foreign direct investment",
        "fdi",
        "current account",
        "sebi",
        "banking",
        "poverty",
        "unemployment",
        "inclusive growth",
        "economic survey",
        "disinvestment",
        "subsidy",
        "interest rate",
    ),
    "geography": (
        "monsoon",
        "cyclone",
        "earthquake",
        "himalaya",
        "ganges",
        "ganga",
        "brahmaputra",
        "drought",
        "flood",
        "glacier",
        "indian ocean",
        "western ghats",
        "deccan",
        "rainfall",
        "soil erosion",
    ),
    "environment": (
        "biodiversity",
        "climate change",
        "ipcc",
        "cop28",
        "cop29",
        "cop30",
        "wildlife",
        "national park",
        "tiger reserve",
        "emission",
        "renewable energy",
        "solar power",
        "environmental impact",
        "pollution",
        "forest cover",
        "wetland",
        "paris agreement",
        "net zero",
    ),
    "history_culture": (
        "freedom struggle",
        "indian national congress",
        "independence movement",
        "archaeological",
        "unesco world heritage",
        "ancient india",
        "medieval india",
        "maurya",
        "mughal",
        "gandhi",
        "ambedkar",
        "partition",
    ),
    "ir_security": (
        "united nations",
        "wto",
        "bilateral",
        "diplomacy",
        "border dispute",
        "defence",
        "g20",
        "brics",
        "quad",
        "terrorism",
        "internal security",
        "nato",
        "foreign policy",
        "treaty",
        "geopolitics",
        "indo-pacific",
    ),
    "science_tech": (
        "isro",
        "spacecraft",
        "vaccine",
        "nuclear",
        "genome",
        "patent",
        "artificial intelligence",
        "cybersecurity",
        "drdo",
        "space mission",
        "semiconductor",
    ),
    "social_sector": (
        "nrega",
        "mgnrega",
        "public distribution",
        "pds",
        "reservation",
        "caste census",
        "gender equality",
        "health mission",
        "education policy",
        "nep 2020",
        "welfare scheme",
        "social justice",
        "sdg",
        "sustainable development",
        "demographics",
        "literacy",
    ),
    "disaster_gov": (
        "ndma",
        "disaster management",
        "landslide",
        "relief fund",
        "early warning",
        "civil defence",
    ),
    "govt_current": (
        "ministry of",
        "union cabinet",
        "gazette",
        "parliamentary",
        "commission report",
        "standing committee",
        "centrally sponsored",
        "flagship scheme",
        "notification",
        "lokpal",
        "right to information",
        "rti act",
    ),
}

# Precompile theme patterns (word-ish; multi-word phrases as literals).
_THEME_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    theme: re.compile(
        r"|".join(re.escape(k) for k in keys),
        re.I,
    )
    for theme, keys in _THEME_KEYWORDS.items()
}

_MIN_THEME_HITS = 2
_MIN_DISTINCT_THEMES = 1


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
    # Property / classified density
    prop = len(re.findall(r"\b(?:₹|rs\.?)\s*\d", text, re.I))
    if prop >= 6 and len(text) < 2000:
        return "ad", "Dense price tokens; treat as classified/ad page."
    return "editorial", "Usable editorial signal."


def is_editorial(page_text: str) -> bool:
    label, _ = classify_page_text(page_text)
    return label == "editorial"


def _theme_hit_map(text: str) -> dict[str, int]:
    lowered = text.lower()
    hits: dict[str, int] = {}
    for theme, pattern in _THEME_PATTERNS.items():
        n = len(pattern.findall(lowered))
        if n:
            hits[theme] = n
    return hits


def classify_exam_relevance(page_text: str) -> tuple[bool, str, str]:
    """Heuristic UPSC / Group-1 / Group-A relevance.

    Returns (relevant, primary_theme_or_empty, rationale).
    Cheap gate — prefer this over LLM for ads; call after editorial pass.
    """
    text = (page_text or "").strip()
    if len(text) < 120:
        return False, "", "Too little text to judge exam relevance."

    theme_hits = _theme_hit_map(text)
    total_hits = sum(theme_hits.values())
    distinct = len(theme_hits)
    off_hits = len(_OFF_SYLLABUS_MARKERS.findall(text))

    primary = ""
    if theme_hits:
        primary = max(theme_hits.items(), key=lambda kv: kv[1])[0]

    # Pure entertainment/sports with no syllabus signal.
    if off_hits >= 2 and total_hits == 0:
        return False, "", f"Off-syllabus markers ({off_hits}); no GS themes."
    if off_hits >= 3 and total_hits < _MIN_THEME_HITS:
        return False, "", f"Lifestyle/sports-heavy ({off_hits}) vs weak GS ({total_hits})."

    if total_hits >= _MIN_THEME_HITS:
        themes = ", ".join(sorted(theme_hits))
        return True, primary, f"GS themes matched ({themes}; hits={total_hits})."

    # Short news grafs often carry one clear GS signal (e.g. inflation + RBI).
    if (
        distinct >= _MIN_DISTINCT_THEMES
        and total_hits >= 1
        and len(text) >= 200
        and off_hits == 0
    ):
        themes = ", ".join(sorted(theme_hits))
        return True, primary, f"GS themes matched ({themes}; hits={total_hits})."

    return False, "", f"No UPSC/Group-1 GS theme match (hits={total_hits})."


def newspaper_page_verdict(page_text: str) -> tuple[str, str]:
    """Combined gate for newspaper MCQ cooking.

    Returns (verdict, rationale) where verdict is one of:
    cook | ad | masthead | low_signal | off_syllabus
    """
    label, rationale = classify_page_text(page_text)
    if label != "editorial":
        return label, rationale
    ok, theme, why = classify_exam_relevance(page_text)
    if not ok:
        return "off_syllabus", why
    theme_bit = f" theme={theme}" if theme else ""
    return "cook", f"Editorial + exam-relevant.{theme_bit} {why}".strip()


def should_cook_newspaper_page(page_text: str) -> bool:
    return newspaper_page_verdict(page_text)[0] == "cook"


# Content-type tag written into page coverage so generation uses UPSC voice.
NEWSPAPER_EXAM_CONTENT_TYPE: Final[str] = "newspaper_upsc"
