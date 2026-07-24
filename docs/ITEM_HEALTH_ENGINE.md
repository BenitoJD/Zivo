# Item Health / Bank Hygiene Engine

**Status:** wired (v1). CTT empirical stage shared with Quality; retirement ETA calls facade.
**Owns the question:** should this live item stay active, be flagged, or retire given learner evidence?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Quality empirical** | CTT flags | `evaluate_empirical` |
| **Item Health** | active|flag|retire | `HealthVerdict` |

---

## 1. Problem + API

### Input
p_correct, n_exposure, optional r_pbis.

### Output
```
HealthVerdict { action: keep|flag|retire, codes, policy_version }
```


Version field: `qb.health.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| CTT item analysis | classic | Assessment Systems | Extreme p + negative r-pbis → broken. |
| Zivo item_retirement | 2026 | item_retirement.py | Exposure floor + near-zero first-try. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.item_health` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `item_health.py` | evaluate_item_health |
| `item_retirement.py` | maps HealthVerdict.retire → retired status |
