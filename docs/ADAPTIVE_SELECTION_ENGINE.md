# Adaptive Selection Engine

**Status:** wired (v1). Deterministic multi-signal scorer owns next-item choice; Elo band + CAT information + mastery need + exposure soft-balance; lineage routing as hard preference when edges exist.  
**Owns the question:** which surviving assertion does this learner see next?  
**Product home:** Question Better / Zivo Learn + Test learn-queue; Adaptive vs Classic study mode; sim Gate 2.

Related: [VISION.md](VISION.md), [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md) (how many), [QUALITY_EVALUATION_ENGINE.md](QUALITY_EVALUATION_ENGINE.md) (which survive), [ADR 0004](adr/0004-swappable-policy-seam.md) (swappable policy seam), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to Budget + Quality

| Engine | Question | Output |
|--------|----------|--------|
| **Budget** | How many items does this page/document need? | `N_page`, `N_doc` |
| **Quality** | Which drafts are fit to count toward that N? | `pass` \| `fail` \| `revise` |
| **Selection** | Which *accepted* item should this learner see *now*? | `assertion_id` + scores + rationale |

Budget plans slots. Quality fills them with instruments. Selection orders the serve path for one learner state. Selection never changes `N_page`, never reopens a quality fail, and never blocks zero-wait refill (`REFILL_BATCH_SIZE` stays pipe chunk only).

```
cook: Budget → generate → Quality gate → bank
serve: learner state + unanswered bank → Selection → one assertion
```

---

## 1. Problem definition

### Input

| Field | Role |
|-------|------|
| `candidates` | Unanswered assertion ids on the active page (sequence order preserved for ties) |
| `LearnerState` | Last outcome, ability (global + per-concept), last assertion/concept |
| `difficulty_by_id` | Online Elo item difficulties (shared logit with ability) |
| `concept_by_id` | Primary concept key per candidate |
| `lineage_by_id` | Soft graph edges from last item (`follow_up_after_miss` / `harder_than`) |
| `exposure_by_id` | Optional serve/answer counts (content balance) |
| `mode` | `learn` vs `test` (weight profile) |
| `policy` | Named seam (`adaptive_v1`, `difficulty_edge`, `concept_reinforce`, `sequence`) |

### Output

```
SelectionVerdict {
  assertion_id: str | None
  scores: SelectionScores | None   # for the chosen item (None on empty / sequence-only)
  rationale: str                   # short machine-stable reason code + human hint
  policy: str
  policy_version: "qb.selection.v1"
  ranked_top: [(id, overall), ...] # optional debug top-k
}
```

| Score | Meaning |
|-------|---------|
| `band_fit` | Closeness to productive-struggle target P≈0.75 (Learn) |
| `information` | 1PL Fisher-like `P(1-P)` at current θ̂ (CAT measurement) |
| `mastery_need` | Prefer under-mastered / just-missed concepts |
| `novelty` | Soft exposure / content-balance prior |
| `lineage` | 1.0 if preferred edge kind matches last outcome, else 0 |
| `overall` | Weighted sum for the active mode |

---

## 2. What research says (synthesis + citations)

### 2.1 CAT: maximum information (and why Learn is not pure CAT)

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Lord; Weiss (practice) | classic | via [EPOD review](https://doi.org/10.21031/epod.1140757) | Mainstream CAT picks the item with **maximum Fisher information** at interim θ̂. |
| Chang & Ying, global / KL information | 1996 | [UMN PDF](https://conservancy.umn.edu/server/api/core/bitstreams/c8355f13-26e5-4c14-a915-e0e6843c8a3f/content) | Early-stage MFI suffers attenuation paradox; **global/KL** information is safer while θ̂ is noisy. |
| Han, non-MFI criteria | 2010 | [UMass NCME PDF](https://www.umass.edu/remp/software/simcata/papers/NCME2010_1_HAN.pdf) | MFI over-picks high-a items; pool utilization needs exposure-aware criteria (GMIR, a-stratification). |
| Barrada et al., ISR comparison | 2009 | [doi:10.1027/1614-2241.5.1.7](https://doi.org/10.1027/1614-2241.5.1.7) | Interval / likelihood-weighted info improve early accuracy vs plain PFI. |

**Implication:** For **Test** (measurement), weight `information` high. For **Learn** (practice), do **not** maximize Fisher at P=0.5; that maximizes measurement efficiency, not desirable difficulty for learning.

### 2.2 Elo / adaptive practice: target success, not max info

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Pelánek et al., Elo for adaptive practice of facts | 2017 | [UMUAI PDF](https://www.fi.muni.cz/~xpelanek/publications/umuai-adaptive-practice.pdf) | Treat answer as learner–item match; Elo updates skill + difficulty online; select for practice goals. |
| Pelánek, Elo in educational systems | 2016 | [CAE PDF](https://www.fi.muni.cz/~xpelanek/publications/CAE-elo.pdf) | Elo is simple, robust, good enough for **adaptive practice / low-stakes**; not a substitute for calibrated IRT in high-stakes CAT. |
| Klinkenberg / Brinkhuis line; dynamic K | 2011–2025 | [PMC12682724](https://pmc.ncbi.nlm.nih.gov/articles/PMC12682724/) | After each update, select next item near ability (often ~50–70% success) so learners stay engaged. |
| ProTuS Elo programming recommendation | 2022 | [ACM 10.1145/3511886](https://dl.acm.org/doi/fullHtml/10.1145/3511886) | Recommend content whose Elo difficulty matches current student rating. |

**Implication (opinionated default):** Learn mode targets **P≈0.75** on the shared Elo logit (existing Zivo band: desirable difficulty / productive struggle). That is Pelánek-style practice selection, not Lord-style CAT. Online Elo already lives in `calibration.py`; selection only *reads* ratings.

### 2.3 Knowledge tracing / mastery: which skill next

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Corbett & Anderson, BKT | 1995 | [Springer](https://link.springer.com/doi/10.1007/BF01099821) | Keep assigning until P(mastered) is high for the skill. |
| Piech et al., Deep Knowledge Tracing | 2015 | [NeurIPS PDF](https://proceedings.neurips.cc/paper_files/paper/2015/file/bac9162b47c56fc8a4d2a519803d51b3-Paper.pdf) | Hidden state predicts gain from next exercise; curricula can prefer expected mastery lift. |
| ExRec / KT+RL recommendation | 2025 | [NeurIPS PDF](https://proceedings.neurips.cc/paper_files/paper/2025/file/13707aad517ddd6c09ea02e0f55e1e7a-Paper-Conference.pdf) | Calibrated KT as environment for exercise recommendation; still needs a policy seam. |

**Implication:** Prefer concepts with **lower** per-concept ability (or a fresh miss) over already-strong strands. Full BKT/DKT can later replace the `mastery_need` feature behind the same seam; v1 uses Elo concept ability + last-outcome reinforce as a cheap KT proxy.

### 2.4 Exposure control / content balancing

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Sympson–Hetter / eligibility (CAT security) | classic–2023 | [doi:10.1007/s41237-023-00214-1](https://doi.org/10.1007/s41237-023-00214-1) | Cap overexposed items; soft eligibility / progressive methods before max-info pick. |
| Han 2010 (above) | 2010 | UMass PDF | Pure MFI burns high-a items; need utilization constraints. |

**Implication:** Soft `novelty` prior `1/(1+exposure)` when counts exist. Never hard-exclude the last remaining candidate (zero-wait / progress integrity). Content balance also nudges toward concepts with fewer answers this session when signals exist.

### 2.5 Cold-start

| Situation | Behavior |
|-----------|----------|
| Empty candidates | `assertion_id=None` |
| Policy `sequence` / Classic | First unanswered (legacy) |
| No difficulties in bank | Degrade to sequence (or concept_reinforce if miss signal only) |
| Ability unknown | θ̂ = 0.0 prior (shared Elo origin) |
| Cold item (no difficulty) | Treat difficulty ≈ learner ability for that concept (explore, never outrank a true band match) |
| Early session (noisy θ̂) | Soft band + mastery/lineage dominate; do not trust sharp MFI alone (Chang & Ying) |

---

## 3. Opinionated defaults (locked for v1)

1. **Learn weights:** band 0.40, mastery 0.25, information 0.15, novelty 0.10, lineage 0.10.  
2. **Test weights:** information 0.40, band 0.20, mastery 0.15, novelty 0.15, lineage 0.10.  
3. **Target success (Learn band):** 0.75 (unchanged from prior `difficulty_edge`).  
4. **Lineage:** if last miss/hit has matching edge into the candidate set, **restrict** to that subset then score (same as today).  
5. **Miss without lineage:** fall through to concept reinforce inside `adaptive_v1` (same concept preferred).  
6. **Deterministic:** no LLM in selection math; ties → earlier sequence order.  
7. **Default policy:** `adaptive_v1`. Classic mode stays `sequence`. Legacy `difficulty_edge` kept for sim Gate 2 (band+lineage only).

---

## 4. Policy seam (ADR 0004)

```
selection_policy = "adaptive_v1"   # product Adaptive default
# also: "difficulty_edge", "concept_reinforce", "sequence"
```

Callers depend on:

```
from app.services.adaptive_selection import select_next, build_learner_state, SelectionVerdict
```

not on ad-hoc ranking in API handlers. Version field: `qb.selection.v1`.

`app/services/selection.py` remains a thin compatibility re-export of leaf helpers / `choose_next_assertion` for sim and older imports.

---

## 5. Algorithm steps (serve)

```
input: candidates (sequence order), LearnerState, signal maps, mode, policy

1. if not candidates → verdict(None, rationale=empty)
2. normalize policy (adaptive → adaptive_v1)
3. if policy == sequence or len==1 → first candidate
4. if policy == concept_reinforce → reinforce/advance by last concept
5. if policy == difficulty_edge → lineage subset → nearest band (legacy Gate 2)
6. if policy == adaptive_v1:
     a. lineage hard filter when edges match last outcome
     b. else if miss + last_concept → soft-prefer same concept in scoring
     c. if no calibrated difficulties → sequence
     d. score each candidate; pick max overall (tie: sequence index)
7. return SelectionVerdict with scores + rationale
```

All O(n) over page candidates. No generation, no LLM.

---

## 6. Fit to Zivo

### 6.1 What exists

| Piece | Role |
|-------|------|
| `calibration.py` | Online Elo ability + item difficulty |
| `selection.py` (legacy) | `sequence` / `concept_reinforce` / `difficulty_edge` |
| `question_pool.select_next_assertion` | Loads DB signals; choose step of learn-queue (no focus/mastery/spaced if-else) |
| Lineage edges at cook | `follow_up_after_miss` / `harder_than` |
| Study mode Adaptive/Classic | Persists `selection_policy` on progress |

### 6.2 What this engine adds

| Artifact | Purpose |
|----------|---------|
| `docs/ADAPTIVE_SELECTION_ENGINE.md` | Durable research + architecture |
| `app/services/adaptive_selection.py` | Pure `select_next` + scores + policies + `narrow_serve_pool` |
| Wire learn-queue / `next_assertion_id` | Single decision path |
| Unit tests on scoring / degrade / lineage | Lock the policy |

Serve hygiene (focus concept, mastery diversify, spaced due prefer, difficulty_edge→reinforce) lives in `select_next` / `LearnerState`, not in `question_pool`.

### 6.3 Hook points

| Hook | Behavior |
|------|----------|
| `build_learn_queue_state` → `select_next_assertion` | Every Learn/Test next-Q |
| `next_assertion_id` | Same engine |
| Sim harness Gate 2 | Still can call `difficulty_edge` explicitly |
| Future admin / debug | Expose `SelectionVerdict.scores` without re-serve |

### 6.4 What not to do

- Do not change Budget N or Quality pass/fail from selection.
- Do not block refill / zero-wait on selection scores.
- Do not require LLM for ranking.
- Do not hard-drop all candidates via exposure (always leave a pick).
- Do not add a parallel next-Q path beside this engine.

---

## 7. Failure modes

| Failure | Behavior |
|---------|----------|
| Empty bank | `None` (queue shows generate/complete) |
| Thin calibration | Degrade to sequence / concept_reinforce |
| Conflicting lineage | Only matching kind used; else band/adaptive |
| All cold items | Sequence order |
| Unknown policy string | Sequence (safe default) |

---

## 8. Measurement: how we know selection is “right”

| Probe | Pass signal |
|-------|-------------|
| Sim Gate 2 | `difficulty_edge` / `adaptive_v1` beat `sequence` on productive-struggle hit rate |
| Serve P̂ | Adaptive Learn sessions land near ~0.70–0.85 first-try (not ~0.50 or ~0.95) |
| Concept balance | After misses, same-concept follow-ups rise vs flat sequence |
| Latency | `select_next` stays sub-ms for page-sized banks |
| Regressions | Unit tests on band, lineage, mastery, degrade |

---

## 9. Open decisions (non-blocking)

Defaults locked for v1:

1. Composite weights above; no RL policy yet.
2. Exposure soft prior only when counts provided.
3. No separate FE required; Adaptive toggle already exists.
4. Full BKT/DKT mastery feature can replace `mastery_need` later behind the same seam.

Revisit when response bank is large enough for empirical weight fit or IRT a-parameters for true 2PL information.

---

## 10. Implementation

| Module | Role |
|--------|------|
| `backend/app/services/adaptive_selection.py` | `select_next`, scores, policies, `qb.selection.v1` |
| `backend/app/services/selection.py` | Compatibility re-exports |
| `backend/app/services/question_pool.py` | DB signal load → engine |
| Tests | `test_adaptive_selection.py` + existing `test_selection.py` |

Durable centerpiece: **Elo practice band for Learn, CAT information for Test, mastery + exposure as soft priors, lineage when the graph speaks; Budget plans N, Quality decides survivors, Selection picks next.**
