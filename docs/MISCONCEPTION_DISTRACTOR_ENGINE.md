# Misconception / Distractor Engine

**Status:** wired (v1). Deterministic distractor scores + Haladyna-aligned codes; LLM labels optional upstream.
**Owns the question:** are distractors incorrect, plausible, and diverse enough to diagnose misconceptions?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Quality** | pass/fail/revise | `uses distractor scores/codes` |
| **Misconception / Distractor** | Distractor fitness | `DistractorVerdict` |

---

## 1. Problem + API

### Input
options, correct indices, optional heuristic flaw codes.

### Output
```
DistractorVerdict { ok: bool, scores: {incorrectness, plausibility, diversity}, flaw_codes, policy_version }
```


Version field: `qb.distractor.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Liusie et al., Distractor Assessment | 2023 | arXiv:2311.04554 | Incorrect + plausible + diverse. |
| Haladyna guidelines | 2002 | AME | Implausible / longest-option cue flaws. |
| Moore et al. | 2023 | arXiv:2307.08161 | Rules beat GPT-4 on IWF detection. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.misconception_distractor` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `misconception_distractor.py` | evaluate_distractors |
| `quality_evaluation / mcq_heuristics` | compose soft distractor codes |
