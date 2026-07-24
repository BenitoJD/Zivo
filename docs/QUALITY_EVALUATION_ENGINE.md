# Quality Evaluation Engine

**Status:** wired (v1). Deterministic decision core owns pass|fail|revise; heuristics and CTT flags own hard fails; LLM critic/verifier supply labels and keyed-answer evidence only.  
**Owns the question:** which draft MCQs survive cook, and which live items should retire after learner evidence?  
**Product home:** Question Better / Zivo Learn + Test + newspaper editions; cook gates, rewrite loop, item retirement. VISION Phase 2 evaluator starts here.

Related: [VISION.md](VISION.md) (Phase 2), [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md) (how many), [ADAPTIVE_SELECTION_ENGINE.md](ADAPTIVE_SELECTION_ENGINE.md) (which next), [ADR 0004](adr/0004-swappable-policy-seam.md) (swappable policy seam), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to the Question Budget Engine

| Engine | Question | Output |
|--------|----------|--------|
| **Budget** | How many items does this page/document need? | `N_page`, `N_doc` |
| **Quality** | Which drafts are fit to count toward that N? | `pass` \| `fail` \| `revise` + flaw codes + scores |
| **Selection** | Which accepted item does this learner see next? | `assertion_id` + scores |

Budget plans slots. Quality decides which instruments fill them. Selection orders the serve path. Cook still stops at `N_page` of *accepted* items; failed drafts do not inflate coverage. Zero-wait / `REFILL_BATCH_SIZE` / budget stop rules stay untouched.

Newspaper editions and ordinary Learn/Test share the same quality gate. Only prompts / content_type styling differ upstream.

---

## 1. Problem definition

### Input

A draft assertion-shaped MCQ plus evidence context:

| Field | Role |
|-------|------|
| `stem` / `question` | Item stem |
| `options` | Answer choices (single or multi-select) |
| `correct_index` / `correct_indices` | Marked key |
| `page_text` / evidence excerpt | Grounding source |
| `prior_mcqs` | Same-page bank for enemy/similarity |
| Optional critic / verify payloads | LLM labels (when sampled) |
| Optional empirical stats | First-try `p`, point-biserial, exposure (post-serve) |

### Output

```
QualityVerdict {
  decision: pass | fail | revise
  flaw_codes: [str]          # stable taxonomy
  scores: { structure, key, distractors, clarity, overall }
  rewrite_brief: str         # hints for rewrite loop / authors
  fatal: bool
  policy_version: "qb.quality.v1"
  stage: cook | empirical    # pre-serve vs post-serve
}
```

| Decision | Meaning for cook | Meaning for retirement |
|----------|------------------|------------------------|
| `pass` | Accept into bank; counts toward `N_page` | Keep `active` |
| `revise` | One rewrite attempt allowed (then re-evaluate) | N/A at cook; authors later |
| `fail` | Reject; do not count toward coverage | Set `retired` (reversible) |

---

## 2. What research says (synthesis + citations)

### 2.1 Item-writing guidelines (canonical taxonomy)

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Haladyna, Downing, Rodriguez | 2002 | [AME 15(3)](https://doi.org/10.1207/s15324818ame1503_5); [PDF](https://cmapspublic3.ihmc.us/rid=1P2XTLCSS-11K09T9-BD5/Haladyna_2002_-Appl_Meas_Educ.pdf) | Validated **31** MC item-writing guidelines from 27 textbooks + 27 studies; content, stem, choices, cueing. |
| Haladyna & Downing | 1989 | taxonomy precursors | Earlier 43-rule set; 2002 reorganizes into classroom-usable bands. |

**Implication:** Flaw codes must map to Haladyna-class rules (negatives, none/all-of-above, longest-option cue, unfocused stem, implausible distractors, ambiguity). Codes are the product vocabulary; prompts and heuristics share them.

### 2.2 Automated IWF detection: rules beat raw LLM judges

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Moore, Nguyen, Chen, Stamper | 2023 | [arXiv:2307.08161](https://arxiv.org/abs/2307.08161); [ECTEL PDF](https://stevenjamesmoore.com/assets/papers/ectel23_full_moore.pdf) | On 19 IWFs / 200 student MCQs: **rule-based matched humans 91%**, GPT-4 **79%**. Rules more interpretable; both stricter than humans. |

**Implication (opinionated default):** Deterministic heuristics **own** structural pass/fail. LLM critic may add soft labels and rewrite briefs; it must **not** override a fatal heuristic fail, and a critic `pass=True` with a fatal code still fails (existing Gate A behavior).

### 2.3 Distractor quality (incorrectness, plausibility, diversity)

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Liusie et al., Distractor Assessment Framework | 2023 | [Eval4NLP](https://doi.org/10.18653/v1/2023.eval4nlp-1.2); [arXiv:2311.04554](https://arxiv.org/abs/2311.04554) | Good distractors are **incorrect**, **plausible**, and **diverse**; automate via binary MRC (incorrectness), confidence mass (plausibility), pairwise embeddings (diversity). |

**Implication:** Cook-time: blind answer-key verify covers incorrectness / unique key; embedding enemy check covers diversity vs prior items; critic labels implausibility. Later: optional pairwise option-similarity score in `scores.distractors`.

### 2.4 Classical test theory: post-serve flags

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Assessment Systems / Iteman practice | n.d. | [CTT item stats](https://assess.com/item-statistics-classical-test-theory/); [item analysis](https://assess.com/item-analysis/) | Flag extreme `p` (too hard/easy); negative point-biserial often means **miskey**; guessing floor ≈ 1/K options. |
| MetricGate; Cogn-IQ discrimination notes | n.d. | [difficulty vs discrimination](https://metricgate.com/blogs/item-difficulty-vs-discrimination/); [discrimination](https://www.cogn-iq.org/learn/theory/item-discrimination/) | Aim r-pbis ≥ ~0.20–0.30; negative = critical; very easy `p>0.95` or very hard `p<0.10` usually discriminate poorly. |
| Ebel bands (via Cogn-IQ / teaching guides) | classic | same | `<0.20` poor; negative → revise/remove. |

**Implication:** Empirical stage is **not** LLM. Retirement uses exposure + first-try correct rate (already in `item_retirement`); engine formalizes CTT thresholds so admin/sim can share them. Cold-start: never retire on tiny N.

### 2.5 LLM-as-judge: use for labels, not sole pass-fail

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Braintrust (practice) | 2025 | [LLM-as-a-judge vs deterministic](https://www.braintrust.dev/articles/what-is-llm-as-a-judge) | Deterministic for format/safety; LLM for nuance. **Never ask factuality without source.** |
| Ye et al. / bias surveys | 2024 | [Justice or Prejudice?](https://arxiv.org/html/2410.02736v1) | Position, verbosity, self-enhancement biases; shuffle / ground / separate judge family. |
| Option-ID selection bias | 2023+ | [PriDe / MCQ brittle evals](https://app.argminai.com/arxiv-dashboard/papers/2309.03882v4) | Models prefer letter IDs; blind verify must not leak marked key (Zivo already does this). |

**Implication:** Verifier is blind to the marked key and grounded in `page_text`. Critic receives source + rubric codes. Sampling rate may be <1.0 for soft paths; fatal heuristics always run. Parse failures: verifier stays conservative (no reject); critic parse fail → fail-safe revise/fail (existing).

### 2.6 AIG quality control pipelines

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| LLM-AIG review | 2025 | [dergipark review PDF](https://dergipark.org.tr/en/download/article-file/4443917) | Post-generation stage filters/ranks; separate eval from generate. |
| AIG quality/usability/validity | 2023 | [PMC10700404](https://pmc.ncbi.nlm.nih.gov/articles/PMC10700404/) | AIG needs explicit quality + validity assessment vs hand-authored items. |
| Technical challenges LLM-AIG | 2025 | [psych journal](https://journal.psych.ac.cn/xlkxjz/EN/10.3724/SP.J.1042.2025.1766) | QC must not rely on same model family alone; hybrid human/AI QA. |

**Implication:** Keep generate ≠ evaluate models where configured (`draft_model_name` vs default critic/verify). Engine is the post-generation filter every cook path calls.

---

## 3. Proposed model (final architecture)

### 3.1 First principles

1. **Structure is law.** Invalid structure, cueing, none/all-of-above, meta references, non-self-contained stems: deterministic fail.
2. **Key integrity is sacred.** Wrong key / multiple defensible / none grounded: fail (verifier), never soft-pass.
3. **LLM labels, formulas decide.** Critic proposes codes + rewrite brief; `decide_verdict` owns the boolean.
4. **Empirical closes the loop.** Near-zero first-try rate after enough exposure → retire (broken more often than merely hard).
5. **Budget and quality are orthogonal.** Quality never changes `N_page`; it only filters yield.

### 3.2 Flaw code taxonomy (stable product vocabulary)

Aligned with existing `FATAL_FLAW_CODES` in `mcq_heuristics.py` (Haladyna-mapped):

| Code | Owner | Typical Haladyna / DAF link |
|------|-------|-----------------------------|
| `invalid_structure` | Heuristic | Formatting / incomplete item |
| `none_or_all_of_above` | Heuristic | Avoid none/all-of-above |
| `negative_wording` | Heuristic | Avoid tricky negatives (unless NOT/EXCEPT capitalized) |
| `longest_option_correct` | Heuristic | Cueing / length clue |
| `unfocused_stem` | Heuristic | Clear focused stem |
| `not_self_contained` | Heuristic | Independent of local page chrome |
| `meta_page_reference` | Heuristic | No book/page exam framing |
| `too_similar_to_prior` | Heuristic + embed | Enemy items / diversity |
| `ambiguous_unclear` | Heuristic + critic | Ambiguity |
| `implausible_distractors` | Critic (+ later DAF) | Plausible distractors |
| `more_than_one_correct` | Verify + critic | Single best answer / key |
| `wrong_answer_key` | Verify | Keyed answer integrity |
| `not_grounded` | Verify + critic | Answerability from source |
| `recognition_only` | Critic (Gate A) | Forces thinking, not keyword spot |
| `grammatical_cues` | Heuristic (soft) | Absolute/grammatical cueing |
| `empirical_non_discriminating` | CTT | r-pbis / extreme p |
| `empirical_likely_broken` | CTT + retirement | Near-zero first-try after exposure |

Fatal set = cook hard-fail. Soft codes contribute to scores and rewrite briefs without immediate reject when alone (except when critic marks them fatal).

### 3.3 Deterministic decision core

```
decide_verdict(signals) → QualityVerdict

1. Collect flaw_codes from heuristic + verify + critic.fatal + similarity.
2. If any code ∈ FATAL_FLAW_CODES (or verify key flaw):
     if rewrite_budget_remaining and stage=cook and not key/structure-hard:
         decision = revise
     else:
         decision = fail
3. Else if critic present and not critique_passes(critic):
     decision = revise if rewrite_budget else fail
4. Else:
     decision = pass
5. scores = aggregate(structure, key, distractors, clarity)
6. rewrite_brief = merge(heuristic messages, critic.rewrite_hints)
```

**`critique_passes` (unchanged product rule, now owned by engine):**

```
pass flag True
AND no fatal_flaws ∩ FATAL_FLAW_CODES
AND flaw_count ≤ 1
```

### 3.4 Scores (0–1, opinionated)

| Score | Formula (v1) |
|-------|----------------|
| `structure` | 1.0 if no structural fatals; else 0.0 |
| `key` | 0.0 if wrong_answer_key / more_than_one_correct / not_grounded; else 1.0 if verify ok or skipped; 0.7 if skipped |
| `distractors` | 1.0 − 0.25×(implausible + none/all + length cue + soft cue flags), floor 0 |
| `clarity` | 1.0 − 0.25×(ambiguous + unfocused + negative + not_self_contained + meta), floor 0 |
| `overall` | min(structure, key) × 0.5 + 0.25×distractors + 0.25×clarity |

Overall is for ranking / admin later; **pass-fail does not threshold on overall** in v1 (avoids Goodhart). Decision stays code-driven.

### 3.5 Empirical stage (CTT)

```
flag_empirical(p_correct, n_exposure, r_pbis=None, n_options=4):

  if n_exposure < MIN_EXPOSURE: return no flag
  if p_correct <= MAX_BROKEN_RATE: empirical_likely_broken → fail/retire
  if r_pbis is not None and r_pbis < 0: empirical_non_discriminating → fail
  if r_pbis is not None and r_pbis < 0.20 and n_exposure >= MIN_RPBIS:
       empirical_non_discriminating → revise/flag (admin; not auto-retire in v1)
  if p_correct >= 0.95 or p_correct <= max(0.10, 1/n_options - eps):
       soft flag only (too easy / too hard)
```

Defaults match current retirement settings: `MIN_EXPOSURE=12`, `MAX_BROKEN_RATE=0.08`.

### 3.6 LLM vs deterministic ownership

| Check | Owner | Why |
|-------|-------|-----|
| Structure, cueing, none/all, self-contained, meta | **Deterministic** | Moore: rules > GPT-4 on IWFs; cheap; testable |
| Enemy / stem similarity | **Deterministic** (string + embed) | Diversity; no judge bias |
| Blind answer key | **LLM verify** (evidence-grounded) | Needs comprehension; key is sacred |
| Ambiguity, recognition_only, implausible distractors | **LLM critic** | Semantic; labels only |
| Pass/fail/revise composition | **Deterministic `decide_verdict`** | Policy seam; unit-tested |
| Post-serve broken item | **Deterministic CTT** | Measurement, not taste |

### 3.7 Policy seam (ADR 0004)

```
quality_policy = "code_driven_v1"   # default
# future: "score_threshold_v2", "ensemble_critic_v3"
```

Callers (`mcq_quality` cook gate, `item_retirement`, future admin API) depend on:

```
from app.services.quality_evaluation import decide_verdict, evaluate_empirical, FATAL_FLAW_CODES
```

not on ad-hoc if-ladders. Version field: `qb.quality.v1`.

---

## 4. Algorithm steps (cook)

```
input: draft MCQ, page_text, prior_mcqs, embeddings, flags (verify_enabled, force_critic, rewrite_budget)

1. heuristic_flaws = run_heuristic_checks(draft, prior_mcqs)
2. if fatal heuristics → decide_verdict → fail (no LLM spend)
3. if too_similar (embed) → fail (too_similar_to_prior)
4. if verify_enabled → verify_answer_key (blind); on flaw → fail
5. maybe run critic (force OR soft heuristic OR sample rate)
6. verdict = decide_verdict(heuristic, verify, critic, similarity, rewrite_budget)
7. if revise and rewrite_budget > 0:
     rewrite with rewrite_brief → goto 1 with budget-1
8. if pass → accept (counts toward N_page)
   if fail → reject (aspect may retry up to MAX_ASPECT_ATTEMPTS)
```

Parallel batch gating and zero-wait first-accept streaming stay in `mcq_quality.py`; they call this engine per draft.

---

## 5. Fit to Zivo

### 5.1 What exists

| Piece | Role |
|-------|------|
| `mcq_heuristics.py` | Pure IWF regex/string checks + `FATAL_FLAW_CODES` |
| `mcq_quality.py` | Generate, verify, critic, rewrite, batch gate |
| `mcq_dedup.py` | Embedding enemy detection |
| `item_retirement.py` | Exposure + first-try rate → `retired` |
| Prompts `mcq_critic_*`, `mcq_verify_*` | LLM rubrics sharing flaw codes |
| Budget engine | Plans `N_page`; cook stops at accepted count |

### 5.2 What this engine adds

| Artifact | Purpose |
|----------|---------|
| `docs/QUALITY_EVALUATION_ENGINE.md` | Durable research + architecture |
| `app/services/quality_evaluation.py` | Pure decide/score/empirical API |
| Wire in `_quality_gate_one` / `_critique_passes` | Single decision path |
| Retirement uses empirical helper | Shared thresholds |
| Unit tests on flaw codes / pass-fail | Lock the policy |

### 5.3 Hook points

| Hook | Behavior |
|------|----------|
| Generate batch gates | Every draft → `decide_verdict` |
| Rewrite loop | Consumes `rewrite_brief` |
| Item retirement ETA | `evaluate_empirical` / shared constants |
| Admin / Phase 2 API (later) | Expose scores + codes without re-cooking |
| Newspaper + Learn/Test | Same engine; content_type only changes generation style |

### 5.4 What not to do

- Do not let critic `pass=True` override fatal codes.
- Do not shrink or grow `N_page` based on yield (budget stays coverage-true; low yield means under-covered until retries/abandon).
- Do not auto-retire on soft CTT flags without exposure floors.
- Do not add a parallel ad-hoc gate beside this engine.

---

## 6. Failure modes

| Failure | Behavior |
|---------|----------|
| Critic unparseable | Treat as fail/revise with `ambiguous_unclear` (existing) |
| Verifier unparseable | Conservative: no reject (existing) |
| All drafts fail | `MAX_ASPECT_ATTEMPTS`; abandon aspect; budget unchanged |
| Tiny empirical N | No retirement |
| Legitimate hard item | High exposure + low p but above broken rate → keep; r-pbis can still soft-flag later |
| Position / ID bias in verify | Blind to marked key; options listed by index |

---

## 7. Measurement: how we know quality is “right”

| Probe | Pass signal |
|-------|-------------|
| Gate yield | Accepted / attempted stable; sudden drop ⇒ prompt or model regression |
| Fatal code mix | Dominated by content issues, not `invalid_structure` spam |
| Verifier disagree rate | Low on held-out human-keyed items |
| Human spot-check | Sampled accepts match Haladyna-acceptable |
| Retirement precision | Spot-check retired items: mostly miskey/ambiguous, not merely hard |
| Learner outcomes | Discrimination of bank rises; abandon on “broken” items falls |

---

## 8. Open decisions (non-blocking)

Defaults locked for v1:

1. Code-driven decide; scores informational.
2. Moore-aligned: heuristics own structural fatals.
3. Critic `flaw_count ≤ 1` soft tolerance kept.
4. Empirical auto-retire only on near-zero first-try + min exposure (existing knobs).
5. No FE required; admin score surface later.

Revisit when labeled outcome bank is large enough for a trained quality model behind the same seam (`quality_policy`).

---

## 9. Implementation

| Module | Role |
|--------|------|
| `backend/app/services/quality_evaluation.py` | `decide_verdict`, scores, `evaluate_empirical`, re-exports `FATAL_FLAW_CODES` |
| `backend/app/services/mcq_heuristics.py` | Leaf heuristics (unchanged ownership of regex) |
| `backend/app/services/mcq_quality.py` | LLM I/O + cook orchestration; calls engine for decisions |
| `backend/app/services/item_retirement.py` | Uses empirical thresholds from engine |
| Tests | `test_quality_evaluation.py` + existing heuristic/gate tests |

Durable centerpiece: **Haladyna-mapped flaw codes + deterministic composition; LLM for grounded key check and soft semantic labels; CTT for post-serve retirement; budget plans N, quality decides survivors.**
