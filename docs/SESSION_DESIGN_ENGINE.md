# Session Design Engine

**Status:** wired (v1). SESSION_SOFT slice over remaining document budget; UX only.
**Owns the question:** how many items should this serve session show before a soft break?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Budget** | N_doc cook plan | `unchanged` |
| **Session Design** | N_session soft cap | `session_soft` |

---

## 1. Problem + API

### Input
remaining_doc_items, mode, optional override soft.

### Output
```
SessionPlan { n_session: int, soft_cap: int, policy_version }
```


Version field: `qb.session.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Duolingo / KA session comfort | practice | Budget doc | SESSION_SOFT ≈ 20. |
| Zivo Budget | 2026 | QUESTION_BUDGET_ENGINE.md | N_session = min(remaining, SESSION_SOFT). |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.session_design` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `session_design.py` | plan_session |
| `build_learn_queue_state` | exposes session_soft |
