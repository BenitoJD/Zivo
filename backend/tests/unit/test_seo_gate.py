"""SEO Gate Engine - usefulness + dedupe verdict shape."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.seo_gate import (
    NEAR_DUPE_COSINE,
    SEO_GATE_VERSION,
    evaluate_dedupe,
    evaluate_usefulness,
)


def test_usefulness_rejects_internal() -> None:
    text = (
        "CONFIDENTIAL - internal only. Meeting notes from standup. "
        "Action items for the team and password rotation schedule. "
        "Do not share outside the company. " * 5
    )
    v = evaluate_usefulness(text, filename="standup-notes.txt")
    assert not v.useful
    assert v.reason in {"internal_markers", "internal_filename", "personal_markers"}
    assert v.policy_version == SEO_GATE_VERSION


def test_usefulness_accepts_explainer() -> None:
    text = (
        "How does a message queue work in system design? "
        "Explain the concept of decoupling producers and consumers, "
        "retries, and poison messages. Why do teams use queues when "
        "the user path should stay fast? This architecture trade-off "
        "shows up in every large interview and exam syllabus. "
    ) * 8
    v = evaluate_usefulness(text, filename="queues.md")
    assert v.useful
    assert v.reason == "ok"


def test_dedupe_ok_when_plumbing_clears() -> None:
    db = MagicMock()
    with patch(
        "app.services.seo_dedupe.check_dedupe",
        return_value=(True, "ok"),
    ) as check:
        v = evaluate_dedupe(
            db,
            fingerprint="queues:abc123",
            title="How queues work",
            lede="A short lede about message queues.",
        )
    check.assert_called_once()
    assert v.ok is True
    assert v.reason == "ok"
    assert v.policy_version == SEO_GATE_VERSION


def test_dedupe_rejects_near_dupe() -> None:
    db = MagicMock()
    with patch(
        "app.services.seo_dedupe.check_dedupe",
        return_value=(False, f"embedding_near_dupe:{NEAR_DUPE_COSINE:.3f}"),
    ):
        v = evaluate_dedupe(
            db,
            fingerprint="queues:abc123",
            title="How queues work",
            lede="A short lede about message queues.",
        )
    assert v.ok is False
    assert "embedding_near_dupe" in v.reason
    assert v.policy_version == SEO_GATE_VERSION


def test_seo_near_dupe_threshold_owned_by_gate() -> None:
    assert NEAR_DUPE_COSINE == 0.85


def test_publish_cap() -> None:
    from app.services.seo_gate import evaluate_publish_cap

    blocked = evaluate_publish_cap(published_today=20, soft_max_per_day=20)
    assert blocked.allow is False and blocked.reason == "soft_max"
    ok = evaluate_publish_cap(published_today=3, soft_max_per_day=20)
    assert ok.allow is True and ok.remaining == 17


class _FixedRng:
    def __init__(self, roll: float, choice: str) -> None:
        self._roll = roll
        self._choice = choice

    def random(self) -> float:
        return self._roll

    def choice(self, seq: list[str]) -> str:
        return self._choice


def test_plan_article_presentation_mix() -> None:
    from app.services.seo_gate import plan_article_presentation

    explainer = plan_article_presentation("general", rng=_FixedRng(0.10, "practice"))
    assert explainer.format == "explainer"
    assert explainer.cta_kind == "practice"
    faq = plan_article_presentation("general", rng=_FixedRng(0.80, "signup"))
    assert faq.format == "faq"
    listing = plan_article_presentation("general", rng=_FixedRng(0.90, "signup"))
    assert listing.format == "list"
    sd = plan_article_presentation("system_design", rng=_FixedRng(0.10, "practice"))
    assert sd.cta_kind == "system_design"
    forced = plan_article_presentation(
        "general", format_override="faq", rng=_FixedRng(0.10, "practice")
    )
    assert forced.format == "faq"


def test_plan_seo_mcq_attach_and_digest() -> None:
    from app.services.seo_gate import NewspaperDigestPage, plan_newspaper_digest, plan_seo_mcq_attach

    rows = [
        ("a", "polity"),
        ("b", "polity"),
        ("c", "economy"),
        ("d", "history"),
        ("e", "geo"),
    ]
    picked = plan_seo_mcq_attach(rows, min_count=4, max_count=6)
    assert picked == ["a", "c", "d", "e"]
    backfill = plan_seo_mcq_attach(rows[:2], min_count=2, max_count=6)
    assert "b" in backfill
    cooked = plan_newspaper_digest(
        [
            NewspaperDigestPage(2, "page-two", 1, True),
            NewspaperDigestPage(1, "page-one", 5, True),
        ],
        max_chars=40,
    )
    assert cooked.startswith("page-one")
    fallback = plan_newspaper_digest(
        [
            NewspaperDigestPage(2, "keep", 0, True),
            NewspaperDigestPage(1, "skip", 0, False),
        ]
    )
    assert fallback == "keep"
    from app.services.seo_gate import (
        plan_seo_article_source_chars,
        plan_seo_faq_item_cap,
        plan_seo_mcq_page_chars,
        plan_seo_related_mcq_query_limit,
    )

    assert plan_seo_faq_item_cap() == 8
    assert plan_seo_related_mcq_query_limit(5) == 5
    assert plan_seo_related_mcq_query_limit(99) == 10
    assert plan_seo_article_source_chars() == 12_000
    assert plan_seo_mcq_page_chars() == 8_000


def test_seo_mcq_attach_ready_and_digest_source() -> None:
    from app.services.seo_gate import (
        SEO_MCQ_ATTACH_MIN,
        evaluate_digest_source_ready,
        evaluate_seo_mcq_attach_ready,
        evaluate_usefulness,
    )

    assert evaluate_seo_mcq_attach_ready(SEO_MCQ_ATTACH_MIN)
    assert not evaluate_seo_mcq_attach_ready(SEO_MCQ_ATTACH_MIN - 1)
    short = evaluate_digest_source_ready("too short")
    assert not short.useful and short.reason == "too_short"
    news = (
        "Parliament passed a bill on inflation and the economy today. "
        "The cabinet discussed policy for rural employment and tax slabs. "
    ) * 12
    ready = evaluate_digest_source_ready(news)
    assert ready.useful
    no_kw = ("The city council met and listed several municipal notices. ") * 20
    digest = evaluate_digest_source_ready(no_kw)
    assert digest.useful
    useful = evaluate_usefulness(no_kw)
    assert not useful.useful and useful.reason == "no_useful_signal"
    from app.services.seo_gate import evaluate_embedding_near_dupe

    dupe = evaluate_embedding_near_dupe(0.90)
    assert dupe.is_dupe
    fresh = evaluate_embedding_near_dupe(0.10)
    assert not fresh.is_dupe
    from app.services.seo_gate import evaluate_newspaper_seo_candidate

    assert not evaluate_newspaper_seo_candidate(
        "Advertisement: buy now limited period offer call toll free flat for sale"
    )


def test_plan_sd_daily_cook() -> None:
    from app.services.seo_gate import plan_sd_daily_cook

    off = plan_sd_daily_cook(cook_enabled=False, sd_published_today=0, cap_allow=True)
    assert off.cook is False and off.reason == "cook_disabled"
    have = plan_sd_daily_cook(cook_enabled=True, sd_published_today=1, cap_allow=True)
    assert have.reason == "already_have_sd"
    need = plan_sd_daily_cook(cook_enabled=True, sd_published_today=0, cap_allow=True)
    assert need.cook is True
    assert need.source_order == ("problem", "topic")


def test_plan_seo_candidate_schedule() -> None:
    from app.services.seo_gate import (
        plan_seo_candidate_schedule,
        seo_candidate_slots_remaining,
    )

    plan = plan_seo_candidate_schedule(batch_size=5)
    assert plan.sources == ("newspaper", "upload")
    assert plan.batch_size == 5
    assert seo_candidate_slots_remaining(accepted=2, batch_size=5) == 3
    assert seo_candidate_slots_remaining(accepted=5, batch_size=5) == 0


def test_plan_seo_fingerprint() -> None:
    from app.services.seo_gate import plan_seo_fingerprint

    assert plan_seo_fingerprint(
        source_kind="sd_bank", source_key="p1", default_fingerprint="x"
    ) == "sd:p1"
    assert plan_seo_fingerprint(
        source_kind="topic_queue", source_key="t1", default_fingerprint="x"
    ) == "topic:t1"
    assert plan_seo_fingerprint(
        source_kind="upload", source_key="u1", default_fingerprint="fp-default"
    ) == "fp-default"


def test_edition_digest_skip_and_cook_tick() -> None:
    from app.services.seo_gate import (
        evaluate_edition_digest_skip_status,
        plan_seo_cook_tick,
    )

    assert evaluate_edition_digest_skip_status("write_failed") == "failed"
    assert evaluate_edition_digest_skip_status("too_short") == "skipped"
    assert plan_seo_cook_tick(limit=3, remaining=10) == 3
    assert plan_seo_cook_tick(limit=8, remaining=10) == 5
    assert plan_seo_cook_tick(limit=8, remaining=2) == 2
    assert plan_seo_cook_tick(limit=8, remaining=0) == 0
    from app.services.seo_gate import SEO_COOK_TICK_DEFAULT

    assert SEO_COOK_TICK_DEFAULT == 3
    assert plan_seo_cook_tick(limit=SEO_COOK_TICK_DEFAULT, remaining=10) == 3
    from app.services.seo_gate import (
        plan_seo_candidate_query_limit,
        plan_seo_digest_writer_contract,
        seo_candidate_min_chars,
    )

    assert seo_candidate_min_chars("newspaper") == 400
    assert seo_candidate_min_chars("upload") == 500
    assert plan_seo_candidate_query_limit(source_kind="newspaper", requested=99) == 20
    digest = plan_seo_digest_writer_contract()
    assert digest.min_words == 400 and digest.max_words == 900 and digest.source_chars == 16000
    from app.services.seo_gate import (
        plan_article_format_contract,
        plan_seo_digest_backfill_batch,
        plan_seo_mcq_attach_defaults,
        plan_seo_sd_source_chars,
    )

    attach = plan_seo_mcq_attach_defaults()
    assert attach.target_count == 4 and attach.min_count == 4
    assert attach.max_count == 6 and attach.generation_attempts == 2
    assert plan_seo_sd_source_chars() == 4000
    assert plan_seo_digest_backfill_batch() == 5
    from app.services.seo_gate import plan_seo_candidate_schedule, plan_seo_related_mcq_query_limit

    assert plan_seo_candidate_schedule().batch_size == 5
    assert plan_seo_related_mcq_query_limit() == 5
    explainer = plan_article_format_contract("explainer")
    assert "800-1500" in explainer.prompt_rule
    assert plan_article_format_contract("nope").format == "explainer"
    from app.services.seo_gate import should_bypass_digest_dedupe

    assert should_bypass_digest_dedupe(force=True, existing_post_id="x")
    assert not should_bypass_digest_dedupe(force=True, existing_post_id=None)
    assert not should_bypass_digest_dedupe(force=False, existing_post_id="x")
