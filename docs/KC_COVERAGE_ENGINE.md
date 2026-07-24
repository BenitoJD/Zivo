# KC / Coverage Label Engine

**Status:** wired (v1). Deterministic aspect normalize + coverage completeness from triage labels.
**Owns the question:** which knowledge components (aspects) does this page expose, and are they covered?
**Product home:** Question Better / Zivo Learn + Test cook/serve/grade loops.

Related: [ENGINES.md](ENGINES.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Budget** | How many items for coverage? | `N_page` |
| **KC / Coverage** | Which KCs/aspects exist and are covered? | `AspectPlan + CoverageState` |
| **Selection** | Which item next among uncovered/weak? | `uses concept keys` |

---

## 1. Problem + API

### Input
Triage aspect list (key, label, centrality) + answered aspect keys.

### Output
```
AspectPlan { aspects: [{key, label, central}], non_content: bool, policy_version }
CoverageState { covered_keys, uncovered_central, complete: bool }
```

### Defaults
- Normalize keys to slug-ish lowercase hyphen.
- `complete` when every central aspect has been asked/answered at least once (Learn) or Budget coverage flags say so.


Version field: `qb.kc.v1`.

---

## 2. Research anchors

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Koedinger et al., Knowledge Components | 2012 | KC framework | Page = bundle of KCs; instruments probe KCs. |
| Shrock & Coscarelli (via assessment practice) | 2007 | domain sampling | 4-6 items/objective for reliable decisions; Learn starts at 1/KC. |
| Zivo Budget engine | 2026 | QUESTION_BUDGET_ENGINE.md | Aspects from triage feed N_page. |

---

## 3. Policy seam (ADR 0004)

Callers depend on `app.services.kc_coverage` facade, not ad-hoc if-ladders. Unknown policy degrades safely.

---

## 4. What not to do

- Do not change Budget `N_page` from this engine unless it owns coverage labeling only.
- Do not block zero-wait refill.
- Do not require LLM for the deterministic core.

---

## 5. Implementation

| Module | Role |
|--------|------|
| `kc_coverage.py` | normalize_aspects, coverage_state, is_page_covered |
| `question_pool.py` | is_coverage_complete / page_coverage consume facade |
