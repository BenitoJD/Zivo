# Spaced Revisit Engine

**Status:** wired (v1). Deterministic spacing schedule from outcome + streak (SM-2-inspired, simplified).
**Owns the question:** when should this learner see this concept/item again?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Mastery** | Stop vs continue now | `session-local` |
| **Spaced Revisit** | When next? | `RevisitPlan` |

---

## 1. Problem + API

### Input
last_correct, prior_interval_hours, ease, repetition count.

### Output
```
RevisitPlan { next_due_hours: float, ease: float, repetitions: int, policy_version }
```


Version field: `qb.space.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Lyle et al.; spaced retrieval | 2019-2024 | Ed Psych / STEM Ed | Spacing beats massing. |
| SM-2 / SuperMemo lineage | classic | spaced repetition | Interval expands after success, shrinks after miss. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.spaced_revisit` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `spaced_revisit.py` | plan_revisit |
| `progress future field` | optional due hints; does not block learn-queue |
