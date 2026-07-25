# Grounding / Answerability Engine

**Status:** wired (v1). Deterministic token-overlap grounding score + verify-signal composition.
**Owns the question:** is the item answerable from the cited page evidence (not world knowledge alone)?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Quality** | fail not_grounded | `uses grounding verdict` |
| **Grounding** | Evidence-answerable? | `GroundingVerdict` |

---

## 1. Problem + API

### Input
stem, correct option text(s), page_text, optional verify_flaw.

### Output
```
GroundingVerdict { grounded: bool, score: float, flaw_codes, fatal: bool, details, policy_version }
```

Cook path: `evaluate_grounding_for_cook` owns `COOK_MIN_SCORE` (0.02) and
`COOK_MIN_PAGE_WORDS` (20). Thin pages stay advisory (`fatal=False`); dense pages
with low overlap get fatal `not_grounded`. Callers must not re-threshold in
`mcq_quality`.

Version field: `qb.ground.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Fu et al., QGEval | 2024 | ACL / arXiv:2406.05707 | Models fail answerability; judge explicitly. |
| Zivo blind verify | 2026 | mcq_quality.verify_answer_key | Key check grounded in page_text. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.grounding_answerability` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `grounding_answerability.py` | evaluate_grounding |
| `quality gate` | fatal not_grounded when score low or verify says so |
