# Content Worthiness Gate Engine

**Status:** wired (v1 deterministic ad/junk; v2 LLM exam-relevance). Deterministic gate from triage signals + text heuristics for ads/junk/empty; exam relevance is an LLM meaning-judgment (cached).
**Owns the question:** is this page/unit worth generating questions for (vs non-content / ads / empty)?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Worthiness** | Cook this unit? | `worthy | skip + reason` |
| **Budget** | N_page=0 when skip | `honest zero` |

---

## 1. Problem + API

### Input
page text excerpt, triage flags (non_content, empty, ad_likely), optional
`newspaper=True` (syllabus/ad heuristics) and `check_junk=True` (letter/word gate).

### Output
```
WorthinessVerdict { worthy: bool, reason: str, policy, policy_version, details }
```


Version field: `qb.worth.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Zivo Budget non_content | 2026 | QUESTION_BUDGET_ENGINE.md | Prefer N=0 over filler. |
| Newspaper ad filter practice | 2026 | newspaper_ad_filter.py | Implementation detail behind this facade. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.content_worthiness.evaluate_worthiness` only
(plus `evaluate_vision_glance` for empty-page multimodal glances).
Do not call `newspaper_ad_filter` or invent junk if-ladders from triage/generation.
Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.
- Do not keep parallel skip paths in `page_triage_graph` / `generation_graph`.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `content_worthiness.py` | `evaluate_worthiness`, `evaluate_vision_glance`, `looks_like_junk` |
| `newspaper_ad_filter.py` | newspaper heuristics (called only from facade) |
| `vision.py` | multimodal glance plumbing (`judge_page_has_content`) |
| `page triage / generation / seo_cook` | skip generate when not worthy |
