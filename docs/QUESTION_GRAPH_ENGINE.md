# Question Graph / Lineage Engine

**Status:** wired (v1). Cook-time edge plan + serve-time successor lookup kinds.
**Owns the question:** which directed relationships exist between assertions for remediation and advance?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Quality** | Surviving items | `bank` |
| **Graph / Lineage** | How items relate | `edge kinds + batch plan` |
| **Selection** | Route miss/hit | `follow_up_after_miss / harder_than` |

---

## 1. Problem + API

### Input
Finalized batch MCQs with concept keys + sequence order.

### Output
```
LineagePlan { edges: [{from_id, to_id, kind}], policy_version }
kind ∈ follow_up_after_miss | harder_than | same_concept
```


Version field: `qb.graph.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Knowledge graph / prerequisite tutoring | classic | ITS literature | Remediation edges after miss; advance after hit. |
| Zivo generation lineage | 2026 | generation_graph | Batch writes follow_up + harder_than. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.question_graph` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `question_graph.py` | plan_batch_lineage, LINK_* constants |
| `generation_graph._write_batch_lineage` | uses plan then persist |
