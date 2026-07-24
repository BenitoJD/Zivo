# Content Worthiness Gate Engine

**Status:** wired (v1). Deterministic gate from triage signals + text heuristics.
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
page text excerpt, triage flags (non_content, empty, ad_likely).

### Output
```
WorthinessVerdict { worthy: bool, reason: str, policy_version }
```


Version field: `qb.worth.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Zivo Budget non_content | 2026 | QUESTION_BUDGET_ENGINE.md | Prefer N=0 over filler. |
| Newspaper ad filter practice | 2026 | newspaper_ad_filter.py | Drop ad-like pages. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.content_worthiness` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `content_worthiness.py` | evaluate_worthiness |
| `page triage / pool` | skip generate when not worthy |
