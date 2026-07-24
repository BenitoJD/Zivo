# Mastery / Evidence-Stop Engine

**Status:** wired (v1). Elo-proxy mastery + Fisher SE stop; serve-time only.
**Owns the question:** has this learner accumulated enough evidence to stop (or pause) on a concept / session?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Calibration** | What is θ̂ / difficulty? | `ratings + n + SE` |
| **Mastery / Evidence-Stop** | Enough evidence to stop? | `StopVerdict` |
| **Session Design** | Soft session length | `SESSION_SOFT interaction` |

---

## 1. Problem + API

### Input
concept_ability, n_responses, cumulative Fisher info (optional), mode.

### Output
```
StopVerdict { stop: bool, reason: mastery|se_precision|min_items|continue, p_mastery, se_theta, policy_version }
```

### Defaults (Learn)
- `p_mastery` logistic from concept Elo vs 0 difficulty prior.
- Stop mastery if `p_mastery >= 0.95` AND `n >= 3`.
- Test: prefer SE stop `se <= 0.40` with `n >= 5`, else continue.


Version field: `qb.mastery.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Corbett & Anderson, BKT | 1995 | Springer | Assign until P(mastered) high. |
| Weiss / CAT SE termination | 1984+ | JCAT termination | Stop when SE(θ) hits target. |
| Beck et al., mastery threshold | 2013 | EDM 2013 | Threshold trades false mastery vs over-practice. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.mastery_evidence` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `mastery_evidence.py` | evaluate_stop, p_mastery_from_ability |
| `learn-queue / future session end` | may surface stop hint; does not shrink Budget |
