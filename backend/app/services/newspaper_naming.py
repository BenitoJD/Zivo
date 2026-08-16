"""Adaptive newspaper PDF naming — aliases first, then LLM, then heuristic.

Filenames and captions change over time. Never hardcode paper abbreviations
as the primary identity path (TH/ET/…). Learn mappings into
``qb.newspaper_paper_alias`` only after a high-confidence, conflict-free LLM hit.

Brand-token families below are a *poison guard* only: they block cross-brand
alias learning (e.g. BS → The Hindu), not a classifier lookup table.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.repositories import newspaper as newspaper_repo
from app.services.llm_json import extract_json_obj

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
# Identity / alias-learn confidence gates (typed verdict below).
LLM_MIN_CONFIDENCE = 0.35
LLM_LEARN_CONFIDENCE = 0.8
NAMING_GATE_VERSION = "qb.newspaper_naming.v1"


@dataclass(frozen=True)
class NamingConfidenceVerdict:
    accept_identity: bool
    learn_alias: bool
    confidence: float
    exact_catalog_hit: bool
    policy_version: str = NAMING_GATE_VERSION


def evaluate_naming_confidence(
    confidence: float,
    *,
    exact_catalog_hit: bool = False,
    min_accept: float = LLM_MIN_CONFIDENCE,
    min_learn: float = LLM_LEARN_CONFIDENCE,
) -> NamingConfidenceVerdict:
    """Whether LLM identity may stand / aliases may be learned.

    Exact catalog hits may accept below min_learn (soft under-score), but soft
    / partial matches still need min_accept. Alias learning needs min_learn
    unless the catalog hit already cleared min_accept.
    """
    conf = float(confidence)
    accept = bool(exact_catalog_hit) or conf >= float(min_accept)
    learn = conf >= float(min_learn) or (
        bool(exact_catalog_hit) and conf >= float(min_accept)
    )
    return NamingConfidenceVerdict(
        accept_identity=accept,
        learn_alias=learn,
        confidence=conf,
        exact_catalog_hit=bool(exact_catalog_hit),
    )


# Conflict guard — tokens that clearly name a brand. Used to reject cross-maps.
_BRAND_TOKEN_FAMILIES: dict[str, frozenset[str]] = {
    "the-hindu": frozenset({"th", "thehindu", "hindu"}),
    "business-standard": frozenset({"bs", "businessstandard"}),
    "hindustan-times": frozenset({"ht", "hindustantimes"}),
    "financial-express": frozenset({"fe", "financialexpress"}),
    "mint": frozenset({"mint", "livemint"}),
    "the-economic-times": frozenset({"et", "economictimes", "theeconomictimes"}),
    "new-indian-express": frozenset({"nie", "newindianexpress", "indianexpress"}),
    "deccan-chronicle": frozenset({"dc", "deccanchronicle"}),
    "new-hans": frozenset({"hans", "newhans"}),
}
_TOKEN_TO_BRAND: dict[str, str] = {
    tok: slug for slug, toks in _BRAND_TOKEN_FAMILIES.items() for tok in toks
}

# Common city / noise tokens to strip when guessing paper title from a filename.
_LOCATION_TOKENS = {
    "kolkata",
    "calcutta",
    "delhi",
    "newdelhi",
    "mumbai",
    "bombay",
    "chennai",
    "madras",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "pune",
    "ahmedabad",
    "jaipur",
    "lucknow",
    "chandigarh",
    "guwahati",
    "patna",
    "bhopal",
    "indore",
    "kochi",
    "cochin",
    "cuttack",
    "hubli",
    "erode",
    "coimbatore",
    "vijayawada",
    "visakhapatnam",
    "vishakapatnam",
    "tirupati",
    "vellore",
    "ranchi",
    "international",
    "epaper",
    "ebook",
    "premium",
    "english",
    "edition",
    "pdf",
}

_DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})[-_.](?P<m>\d{1,2})[-_.](?P<d>\d{1,2})"),
    re.compile(r"(?P<d>\d{1,2})[-_.](?P<m>\d{1,2})[-_.](?P<y>20\d{2})"),
    re.compile(r"(?P<d>\d{1,2})[-_.](?P<m>\d{1,2})[-_.](?P<y>\d{2})\b"),
]


@dataclass(frozen=True)
class ParsedEdition:
    paper_slug: str
    paper_title: str
    edition_date: date
    location_raw: str
    source: str  # alias | llm | heuristic (+ date source)


def _normalize_blob(filename: str, caption: str) -> str:
    base = (filename or "").rsplit("/", 1)[-1]
    base = choose(base.lower().endswith(".pdf"), base[:-4], base)
    return f"{base} {caption or ''}".strip()


def _date_from_match(m: re.Match[str]) -> date | None:
    y = int(m.group("y"))
    y = choose(y < 100, y + 2000, y)
    try:
        return date(y, int(m.group("m")), int(m.group("d")))
    except ValueError:
        return None


def _parse_date(blob: str) -> date | None:
    # Collapse fancy separators (‹ › / …) between digits so DD‹MM‹YYYY still parses.
    normalized = re.sub(r"(?<=\d)[^\dA-Za-z]+(?=\d)", "-", (blob or "").replace(" ", "_"))

    def _try_pat(pat: re.Pattern[str]) -> date | None:
        m = pat.search(normalized)
        return pick(not m, lambda: None, lambda: _date_from_match(m))

    return next(filter(None, map(_try_pat, _DATE_PATTERNS)), None)


def cheap_paper_slug_hint(
    db: Session,
    *,
    filename: str,
    caption: str = "",
) -> str | None:
    """Alias / filename-token brand hint without LLM (reconcile + allowlist gate)."""
    blob, tokens, _, _, _, guessed_title = _date_and_tokens(
        filename=filename,
        caption=caption,
        message_date=datetime.now(timezone.utc),
    )
    hit = next(
        filter(
            None,
            (
                newspaper_repo.resolve_alias(db, raw)
                for raw in _alias_lookup_keys(
                    guessed_title=guessed_title, blob=blob, tokens=tokens
                )
            ),
        ),
        None,
    )
    brands = _filename_brand_slugs(tokens, blob=blob)
    return pick(
        bool(hit),
        lambda: hit[0],
        lambda: pick(len(brands) == 1, lambda: next(iter(brands)), lambda: None),
    )


def resolve_edition_date(
    *,
    filename: str,
    caption: str = "",
    message_date: datetime | None = None,
) -> date:
    """Edition calendar day: filename/caption date wins; else message.date in IST."""
    parsed = _parse_date(_normalize_blob(filename, caption))

    def _from_message() -> date:
        md = message_date or datetime.now(timezone.utc)
        md = pick(md.tzinfo is None, lambda: md.replace(tzinfo=timezone.utc), lambda: md)
        return md.astimezone(_IST).date()

    return pick(parsed is not None, lambda: parsed, _from_message)


def _guess_location(tokens: list[str]) -> str:
    noise = {
        "epaper",
        "ebook",
        "premium",
        "english",
        "edition",
        "pdf",
        "international",
    }
    return next(
        filter(
            lambda t: t.lower() in _LOCATION_TOKENS and t.lower() not in noise,
            tokens,
        ),
        "",
    )


def _guess_title(tokens: list[str]) -> str:
    kept = list(
        filter(
            lambda t: (
                t.lower() not in _LOCATION_TOKENS
                and not re.fullmatch(r"\d{1,4}", t)
                and not re.fullmatch(r"20\d{2}", t)
            ),
            tokens,
        )
    )
    parts = [choose(len(t) <= 3, t.upper(), t.title()) for t in kept[:6]]
    return pick(not kept, lambda: "Newspaper", lambda: " ".join(parts))


def _norm_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (raw or "").lower())


def _brand_token_from_norm(norm: str) -> str | None:
    """Map a normalized token (possibly glued city/date) onto a brand-family key."""

    def _scan() -> str | None:
        loc_norms = {_norm_key(t) for t in _LOCATION_TOKENS}
        toks = filter(
            lambda tok: not (len(tok) < 2 or not norm.startswith(tok) or norm == tok),
            sorted(_TOKEN_TO_BRAND, key=len, reverse=True),
        )

        def _ok(tok: str) -> bool:
            rest = norm[len(tok) :]
            return rest.isdigit() or any(rest.startswith(loc) for loc in loc_norms)

        return next(filter(_ok, toks), None)

    return pick(
        not norm,
        lambda: None,
        lambda: pick(norm in _TOKEN_TO_BRAND, lambda: norm, _scan),
    )


def _extracted_brand_tokens(tokens: list[str]) -> list[str]:
    """Brand-family keys present in filename tokens, including glued forms."""
    seen: set[str] = set()

    def _unseen(bt: str) -> bool:
        return pick(bt in seen, lambda: False, lambda: seen.add(bt) or True)

    bts = filter(None, (_brand_token_from_norm(_norm_key(t)) for t in tokens))
    return list(filter(_unseen, bts))


def _paper_id_fingerprint(*, filename: str, caption: str) -> str:
    """City/date-stripped identity for paper-id LLM cache sharing across editions."""
    blob = _normalize_blob(filename, caption)
    tokens = list(filter(None, re.split(r"[_\-\s.]+", blob)))
    raw_parts = [
        (_brand_token_from_norm(_norm_key(t)) or _norm_key(t))
        for t in filter(
            lambda t: (
                t.lower() not in _LOCATION_TOKENS
                and not re.fullmatch(r"\d{1,8}", t)
                and not re.fullmatch(r"20\d{2}", t)
            ),
            tokens,
        )
    ]
    parts = list(filter(lambda p: p and not p.isdigit(), raw_parts))
    return pick(
        bool(parts),
        lambda: "|".join(sorted(set(parts))),
        lambda: _norm_key(blob)[:64] or "unknown",
    )


def _alias_lookup_keys(*, guessed_title: str, blob: str, tokens: list[str]) -> list[str]:
    keys = [guessed_title, blob]
    keys += pick(bool(tokens), lambda: [tokens[0]], lambda: [])
    # Glued city PDFs (thdelhi24072026) must still hit a learned "th" alias.
    keys.extend(_extracted_brand_tokens(tokens))
    blob_brand = _brand_token_from_norm(_norm_key(blob))
    keys += pick(bool(blob_brand), lambda: [blob_brand], lambda: [])
    seen: set[str] = set()

    def _keep(k: str) -> bool:
        norm = _norm_key(k)
        return pick(
            not norm or norm in seen,
            lambda: False,
            lambda: seen.add(norm) or True,
        )

    return list(filter(_keep, keys))


def _match_known_brand(db: Session, paper_title: str) -> tuple[str, str, bool]:
    """Map LLM title onto an existing brand by exact slug/title only.

    Returns (slug, title, exact_catalog_hit). Soft/partial substring matches are
    intentionally rejected — they poisoned aliases (e.g. BS → The Hindu).
    """
    title = re.sub(r"\s+", " ", (paper_title or "").strip()) or "Newspaper"
    slug, canon = newspaper_repo.make_paper_identity(title)
    title_l = title.lower()

    def _hit(b: dict) -> tuple[str, str, bool] | None:
        b_title = str(b.get("paper_title") or "")
        b_slug = str(b.get("paper_slug") or "")
        return pick(
            b_slug == slug or b_title.lower() == title_l,
            lambda: (b_slug, b_title or canon, True),
            lambda: None,
        )

    return next(
        filter(None, map(_hit, newspaper_repo.list_brands(db))),
        (slug, canon, False),
    )


def _filename_brand_slugs(tokens: list[str], *, blob: str = "") -> set[str]:
    """Brand families clearly named by filename tokens (conflict guard)."""
    found: set[str] = set()
    norms = {_norm_key(t) for t in filter(None, tokens)}
    blob_n = _norm_key(blob)
    norms = pick(bool(blob_n), lambda: norms | {blob_n}, lambda: norms)

    loc_norms = {_norm_key(t) for t in _LOCATION_TOKENS}

    def _add_glued(norm: str) -> None:
        items = filter(
            lambda item: not (
                len(item[0]) < 2 or not norm.startswith(item[0]) or norm == item[0]
            ),
            _TOKEN_TO_BRAND.items(),
        )

        def _ok(item: tuple[str, str]) -> bool:
            tok, _slug = item
            rest = norm[len(tok) :]
            return rest.isdigit() or any(rest.startswith(loc) for loc in loc_norms)

        for _tok, slug in filter(_ok, items):
            found.add(slug)

    def _consume(norm: str) -> None:
        brand = _TOKEN_TO_BRAND.get(norm)
        pick(
            bool(brand),
            lambda: found.add(brand),
            lambda: _add_glued(norm),
        )

    for norm in norms:
        _consume(norm)
    return found


def _identity_conflicts_filename(
    *, paper_slug: str, tokens: list[str], blob: str
) -> str | None:
    """Return conflicting brand slug if filename clearly names another paper."""
    named = _filename_brand_slugs(tokens, blob=blob)
    others = named - {paper_slug}
    return pick(
        not named,
        lambda: None,
        lambda: pick(
            paper_slug in named and len(named) == 1,
            lambda: None,
            lambda: pick(bool(others), lambda: sorted(others)[0], lambda: None),
        ),
    )


def _learnable_alias_keys(
    *,
    paper_slug: str,
    guessed_title: str,
    tokens: list[str],
) -> list[str]:
    """Only brand-family tokens present in the filename — never cities/dates/noise."""
    loc_norms = {_norm_key(t) for t in _LOCATION_TOKENS}
    family = _BRAND_TOKEN_FAMILIES.get(paper_slug, frozenset())

    def _unknown_brand() -> list[str]:
        parts = list(
            filter(
                lambda p: p
                and _norm_key(p) not in loc_norms
                and not _norm_key(p).isdigit(),
                re.split(r"\s+", (guessed_title or "").strip()),
            )
        )
        key = _norm_key(" ".join(parts))
        return pick(
            bool(key) and len(key) >= 3 and not re.fullmatch(r"\d{6,8}", key),
            lambda: [" ".join(parts)],
            lambda: [],
        )

    def _known_family() -> list[str]:
        seen: set[str] = set()

        def _keep(raw: str) -> bool:
            norm = _norm_key(raw)
            bad = (
                not norm
                or norm in seen
                or norm in loc_norms
                or norm.isdigit()
                or bool(re.fullmatch(r"\d{6,8}", norm))
                or norm not in family
            )
            return pick(bad, lambda: False, lambda: seen.add(norm) or True)

        return list(
            filter(_keep, [guessed_title, *tokens, *_extracted_brand_tokens(tokens)])
        )

    return pick(not family, _unknown_brand, _known_family)


def _learn_aliases(
    db: Session,
    *,
    paper_slug: str,
    paper_title: str,
    guessed_title: str,
    tokens: list[str],
    confidence: float,
    exact_catalog_hit: bool,
    blob: str,
) -> None:
    """Cache learned mappings — high confidence + no cross-brand conflict only."""
    gate = evaluate_naming_confidence(
        confidence, exact_catalog_hit=exact_catalog_hit
    )

    def _upsert_all() -> None:
        for key in _learnable_alias_keys(
            paper_slug=paper_slug, guessed_title=guessed_title, tokens=tokens
        ):
            newspaper_repo.upsert_alias(
                db, alias_key=key, paper_slug=paper_slug, paper_title=paper_title
            )

    def _do_learn() -> None:
        conflict = _identity_conflicts_filename(
            paper_slug=paper_slug, tokens=tokens, blob=blob
        )
        pick(
            bool(conflict),
            lambda: logger.info(
                "skip alias learn paper=%s — filename brand conflict", paper_slug
            ),
            _upsert_all,
        )

    pick(
        not gate.learn_alias,
        lambda: logger.info(
            "skip alias learn paper=%s confidence=%s exact=%s",
            paper_slug,
            confidence,
            exact_catalog_hit,
        ),
        _do_learn,
    )


def _known_brands_prompt(db: Session) -> str:
    brands = newspaper_repo.list_brands(db)
    return pick(
        not brands,
        lambda: "(none yet — invent a clear canonical English title)",
        lambda: "\n".join(
            f"- {b['paper_title']} (slug={b['paper_slug']}, "
            f"{choose(bool(b.get('enabled')), 'enabled', 'disabled')})"
            for b in brands
        ),
    )


async def _llm_identify_paper_async(
    db: Session,
    *,
    filename: str,
    caption: str,
    tokens: list[str] | None = None,
    blob: str = "",
) -> tuple[str, str, float, bool] | None:
    """Ask LLM what newspaper this PDF is. No hardcoded abbreviation tables.

    Caller must hold the LLM slot (``complete_chat`` / ``run_coro_in_worker`` /
    ``llm_slot_async``).

    Returns (slug, title, confidence, exact_catalog_hit) or None.
    """
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put
    from app.services.llm_registry import default_chat_model_id
    from app.services.llm_router import acomplete_chat

    tok_list = pick(
        tokens is not None,
        lambda: tokens,
        lambda: list(
            filter(None, re.split(r"[_\-\s.]+", _normalize_blob(filename, caption)))
        ),
    )
    blob_s = blob or _normalize_blob(filename, caption)
    fp = _paper_id_fingerprint(filename=filename, caption=caption)
    cache_key = content_hash_key("newspaper_paper_id", fp)
    hit = cache_get(db, kind="newspaper_paper_id", cache_key=cache_key)

    def _confidence_of(raw_conf: Any) -> float:
        try:
            return float(choose(raw_conf is not None, raw_conf, 0))
        except (TypeError, ValueError):
            return 0.0

    def _from_cache() -> tuple[str, str, float, bool] | None:
        slug = str(hit["slug"])
        canon = str(hit["title"])
        confidence = _confidence_of(hit.get("confidence"))
        exact_hit = bool(hit.get("exact_hit"))
        return pick(
            not _identity_conflicts_filename(
                paper_slug=slug, tokens=tok_list, blob=blob_s
            ),
            lambda: (slug, canon, confidence, exact_hit),
            lambda: None,
        )

    cached = pick(
        isinstance(hit, dict) and bool(hit.get("slug")) and bool(hit.get("title")),
        _from_cache,
        lambda: None,
    )

    async def _cached() -> tuple[str, str, float, bool] | None:
        return cached

    async def _call_llm() -> tuple[str, str, float, bool] | None:
        system = (
            "You identify Indian / English newspaper brands from Telegram PDF filenames "
            "and captions. Filenames change often (abbreviations, cities, dates). "
            "Expand common abbreviations using world knowledge (e.g. brand initials). "
            "Never treat a city or date as the newspaper name. "
            "Prefer matching a known catalog brand when the file clearly refers to it. "
            "When the filename clearly matches a known brand (including common initials), "
            "set confidence >= 0.8. "
            "Never map a clear other-brand abbreviation onto a different catalog brand "
            "(e.g. BS/Business Standard, HT, FE, Mint, ET are not The Hindu). "
            "Reply with JSON only — no prose."
        )
        user = (
            f"Known brands in our catalog:\n{_known_brands_prompt(db)}\n\n"
            f"Filename: {filename or '(none)'}\n"
            f"Caption: {caption or '(none)'}\n\n"
            'Return JSON only: {"paper_title":"<canonical English newspaper name>",'
            '"confidence":<0.0-1.0>}'
        )
        try:
            model_id = default_chat_model_id(db)
            raw = await acomplete_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                db,
                model_id=model_id,
                log_tag="newspaper_paper_id",
            )
        except Exception:
            logger.exception("newspaper paper-id LLM failed")
            return None

        data = extract_json_obj(raw)
        title = str(data.get("paper_title") or "").strip()
        confidence = _confidence_of(data.get("confidence"))

        def _empty() -> None:
            logger.info("newspaper paper-id empty title confidence=%s", confidence)
            return None

        def _after_title() -> tuple[str, str, float, bool] | None:
            slug, canon, exact_hit = _match_known_brand(db, title)
            conflict = _identity_conflicts_filename(
                paper_slug=slug, tokens=tok_list, blob=blob_s
            )

            def _reject_conflict() -> None:
                logger.info(
                    "newspaper paper-id rejected title=%r slug=%s — filename names %s",
                    title,
                    slug,
                    conflict,
                )
                return None

            def _after_conflict_clear() -> tuple[str, str, float, bool] | None:
                gate = evaluate_naming_confidence(
                    confidence, exact_catalog_hit=exact_hit
                )

                def _reject_gate() -> None:
                    logger.info(
                        "newspaper paper-id rejected title=%r confidence=%s",
                        title,
                        confidence,
                    )
                    return None

                def _cache_and_return() -> tuple[str, str, float, bool]:
                    try:
                        cache_put(
                            db,
                            kind="newspaper_paper_id",
                            cache_key=cache_key,
                            value={
                                "slug": slug,
                                "title": canon,
                                "confidence": confidence,
                                "exact_hit": exact_hit,
                            },
                        )
                    except Exception:
                        logger.debug(
                            "newspaper paper-id cache write failed", exc_info=True
                        )
                    return slug, canon, confidence, exact_hit

                return pick(not gate.accept_identity, _reject_gate, _cache_and_return)

            return pick(bool(conflict), _reject_conflict, _after_conflict_clear)

        return pick(not title, _empty, _after_title)

    return await pick(cached is not None, _cached, _call_llm)


def _llm_identify_paper_sync(
    db: Session,
    *,
    filename: str,
    caption: str,
    tokens: list[str] | None = None,
    blob: str = "",
) -> tuple[str, str, float, bool] | None:
    """Sync path — ``run_coro_in_worker`` owns the LLM slot."""
    from app.services.llm_sync import run_coro_in_worker

    try:
        return run_coro_in_worker(
            _llm_identify_paper_async(
                db,
                filename=filename,
                caption=caption,
                tokens=tokens,
                blob=blob,
            )
        )
    except Exception:
        logger.exception("newspaper paper-id sync LLM failed")
        return None


_IDENTITY_RULES = (
    Rule(when=(Pred("n", "eq", 4),), action="rich"),
    Rule(when=(Pred("n", "eq", 2),), action="pair"),
    Rule(when=(), action="none"),
)


def _finish_parse(
    db: Session,
    *,
    filename: str,
    caption: str,
    edition_date: date,
    date_source: str,
    location_raw: str,
    guessed_title: str,
    blob: str,
    tokens: list[str],
    llm_identity: tuple[str, str] | tuple[str, str, float, bool] | None,
    allow_llm: bool,
) -> ParsedEdition:
    def _try_alias(raw: str) -> ParsedEdition | None:
        alias = newspaper_repo.resolve_alias(db, raw)

        def _poison() -> None:
            logger.warning(
                "ignoring poisoned alias key=%r -> %s (filename names %s)",
                raw,
                alias[0],
                _identity_conflicts_filename(
                    paper_slug=alias[0], tokens=tokens, blob=blob
                ),
            )
            newspaper_repo.delete_alias(db, raw)
            return None

        def _edition() -> ParsedEdition:
            return ParsedEdition(
                paper_slug=alias[0],
                paper_title=alias[1],
                edition_date=edition_date,
                location_raw=location_raw,
                source=f"alias+{date_source}",
            )

        def _checked() -> ParsedEdition | None:
            conflict = _identity_conflicts_filename(
                paper_slug=alias[0], tokens=tokens, blob=blob
            )
            return pick(bool(conflict), _poison, _edition)

        return pick(not alias, lambda: None, _checked)

    alias_edition = next(
        filter(
            None,
            map(
                _try_alias,
                _alias_lookup_keys(
                    guessed_title=guessed_title, blob=blob, tokens=tokens
                ),
            ),
        ),
        None,
    )

    def _heuristic() -> ParsedEdition:
        slug, title = newspaper_repo.make_paper_identity(guessed_title)
        return ParsedEdition(
            paper_slug=slug,
            paper_title=title,
            edition_date=edition_date,
            location_raw=location_raw,
            source=f"heuristic+{date_source}",
        )

    def _llm_edition(
        slug: str, title: str, confidence: float, exact_hit: bool
    ) -> ParsedEdition:
        _learn_aliases(
            db,
            paper_slug=slug,
            paper_title=title,
            guessed_title=guessed_title,
            tokens=tokens,
            confidence=confidence,
            exact_catalog_hit=exact_hit,
            blob=blob,
        )
        return ParsedEdition(
            paper_slug=slug,
            paper_title=title,
            edition_date=edition_date,
            location_raw=location_raw,
            source=f"llm+{date_source}",
        )

    def _pair_identity(
        identity: tuple[str, str],
    ) -> dict[str, Any]:
        slug, title = identity
        conflict = _identity_conflicts_filename(
            paper_slug=slug, tokens=tokens, blob=blob
        )

        def _reject() -> dict[str, Any]:
            logger.info(
                "injected identity %s rejected — filename names %s",
                slug,
                conflict,
            )
            return {
                "identity": None,
                "confidence": LLM_LEARN_CONFIDENCE,
                "exact_hit": True,
            }

        return pick(
            bool(conflict),
            _reject,
            lambda: {
                "identity": (slug, title),
                "confidence": LLM_LEARN_CONFIDENCE,
                "exact_hit": True,
            },
        )

    def _normalize(identity: Any) -> dict[str, Any]:
        n = pick(identity is None, lambda: 0, lambda: len(identity))
        hit = first_match(_IDENTITY_RULES, {"n": n})
        return apply(
            hit.action,
            {
                "rich": lambda: {
                    "identity": (identity[0], identity[1]),
                    "confidence": identity[2],
                    "exact_hit": identity[3],
                },
                "pair": lambda: _pair_identity(identity),
                "none": lambda: {
                    "identity": None,
                    "confidence": LLM_LEARN_CONFIDENCE,
                    "exact_hit": True,
                },
            },
        )

    def _without_alias() -> ParsedEdition:
        normed = _normalize(llm_identity)
        identity = normed["identity"]
        confidence = normed["confidence"]
        exact_hit = normed["exact_hit"]

        def _maybe_llm() -> dict[str, Any]:
            rich = _llm_identify_paper_sync(
                db, filename=filename, caption=caption, tokens=tokens, blob=blob
            )
            return pick(
                bool(rich),
                lambda: {
                    "identity": (rich[0], rich[1]),
                    "confidence": rich[2],
                    "exact_hit": rich[3],
                },
                lambda: {
                    "identity": None,
                    "confidence": confidence,
                    "exact_hit": exact_hit,
                },
            )

        filled = pick(
            identity is None and allow_llm,
            _maybe_llm,
            lambda: {
                "identity": identity,
                "confidence": confidence,
                "exact_hit": exact_hit,
            },
        )
        return pick(
            bool(filled["identity"]),
            lambda: _llm_edition(
                filled["identity"][0],
                filled["identity"][1],
                filled["confidence"],
                filled["exact_hit"],
            ),
            _heuristic,
        )

    return pick(alias_edition is not None, lambda: alias_edition, _without_alias)


def _date_and_tokens(
    *,
    filename: str,
    caption: str,
    message_date: datetime,
) -> tuple[str, list[str], date, str, str, str]:
    blob = _normalize_blob(filename, caption)
    tokens = list(filter(None, re.split(r"[_\-\s.]+", blob)))
    from_file = _parse_date(blob)
    edition_date, date_source = pick(
        from_file is not None,
        lambda: (from_file, "filename"),
        lambda: (
            resolve_edition_date(
                filename=filename, caption=caption, message_date=message_date
            ),
            "message_date",
        ),
    )
    location_raw = _guess_location(tokens)
    guessed_title = _guess_title(tokens)
    return blob, tokens, edition_date, date_source, location_raw, guessed_title


def parse_edition_meta(
    db: Session,
    *,
    filename: str,
    caption: str,
    message_date: datetime,
    llm_identity: tuple[str, str] | None = None,
    skip_llm: bool = False,
) -> ParsedEdition:
    """Sync parse. Tests: ``skip_llm=True`` or pass ``llm_identity``."""
    blob, tokens, edition_date, date_source, location_raw, guessed_title = _date_and_tokens(
        filename=filename, caption=caption, message_date=message_date
    )
    return _finish_parse(
        db,
        filename=filename,
        caption=caption,
        edition_date=edition_date,
        date_source=date_source,
        location_raw=location_raw,
        guessed_title=guessed_title,
        blob=blob,
        tokens=tokens,
        llm_identity=llm_identity,
        allow_llm=not skip_llm and llm_identity is None,
    )


async def parse_edition_meta_async(
    db: Session,
    *,
    filename: str,
    caption: str,
    message_date: datetime,
) -> ParsedEdition:
    """Async parse for Telethon ingest — awaits LLM without nested event loops."""
    blob, tokens, edition_date, date_source, location_raw, guessed_title = _date_and_tokens(
        filename=filename, caption=caption, message_date=message_date
    )
    alias_hit = next(
        filter(
            None,
            (
                newspaper_repo.resolve_alias(db, raw)
                for raw in _alias_lookup_keys(
                    guessed_title=guessed_title, blob=blob, tokens=tokens
                )
            ),
        ),
        None,
    )

    async def _via_alias() -> ParsedEdition:
        return _finish_parse(
            db,
            filename=filename,
            caption=caption,
            edition_date=edition_date,
            date_source=date_source,
            location_raw=location_raw,
            guessed_title=guessed_title,
            blob=blob,
            tokens=tokens,
            llm_identity=None,
            allow_llm=False,
        )

    async def _via_llm() -> ParsedEdition:
        from app.eta.llm_concurrency import llm_slot_async

        async with llm_slot_async():
            llm_identity = await _llm_identify_paper_async(
                db, filename=filename, caption=caption, tokens=tokens, blob=blob
            )
        return _finish_parse(
            db,
            filename=filename,
            caption=caption,
            edition_date=edition_date,
            date_source=date_source,
            location_raw=location_raw,
            guessed_title=guessed_title,
            blob=blob,
            tokens=tokens,
            llm_identity=llm_identity,
            allow_llm=False,
        )

    return await pick(bool(alias_hit), _via_alias, _via_llm)
