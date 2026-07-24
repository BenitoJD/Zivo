# Question Budget Engine

**Status:** wired (v1). Planner owns `N_page`; cook stops at plan; `REFILL_BATCH_SIZE` is pipe chunk only.  
**Owns the question:** how many questions are needed for this page, and for this whole document?  
**Product home:** Question Better / Zivo Learn + Test; generation cook, verify/critic gates, Elo calibration, page-scoped pools.

Related: [VISION.md](VISION.md), [DATA_MODEL.md](DATA_MODEL.md), [WORKSPACE.md](WORKSPACE.md), [ADR 0004](adr/0004-swappable-policy-seam.md) (swappable policy seam).

---

## 0. Product rule: newspaper editions (Jobs)

**Source of truth for this rule lives here** (not a separate ADR). Workspace chrome follows it.

| Surface | Mode | Behavior |
|---------|------|----------|
| Open a newspaper edition | **Learn** (`mode=learn`) | Land straight in Learn MCQ chrome. **No Learn/Test chooser modal** on every open. |
| **Test this edition** | **Test** (`mode=test`) | Explicit, quieter secondary CTA (catalog or sidebar Learn/Test toggle). Never a blocking popup. |

Same budget engine for both. Only the mode multiplier / document info floor differs (`m_learn=1` vs `m_test_formative=3`; Test `N_doc` may raise via `N_info`).

- **Cook / triage / pool default** for editions: `mode=learn` (coverage dose).
- **Test path** passes `mode=test` into `plan_page_budget` / `plan_document_budget` (serve + optional expand cook).
- Ordinary PDF artifacts keep Settings → Relaxed/Exam preference for initial workspace mode. Newspapers **ignore** that preference on open and always start Learn.

---

## 1. Problem definition

### Two budgets, one planner

| Budget | Question it answers | Consumer |
|--------|---------------------|----------|
| **Page budget** `N_page` | How many distinct, cookable items should exist for this page before we call coverage complete? | Triage → pool cook → UI “Question X of Y” |
| **Document budget** `N_doc` | How many items across the selected page range (or whole artifact) are planned in total? | Cost/ETA planning, Test session design, “document complete” |

They are not rivals. **Page is the atomic planning unit** (matches today’s page-scoped generation and newspaper edition pages). **Document is the sum**, with optional soft session slices for UX.

### Reconciliation rule (opinionated default)

```
N_doc = Σ_{p ∈ cookable pages} N_page(p)
N_session = min( remaining(N_doc), SESSION_SOFT )   # serve pacing only; does not shrink cook plan
```

- **Cook plan** follows coverage + measurement targets (can be large for a dense textbook chapter).
- **Session slice** is a soft UX cap (order ~15–25 items) so learners are not shown “Question 3 of 400.” Progress UI should show **page** Y from `N_page`, and optionally a separate document progress from answered / `N_doc`.
- Never reconcile by inventing filler on thin pages. Prefer `N_page = 0` (`non_content`) over padding.
- If a hard product ceiling is required (cost), cut **peripheral** units first, never invent quota.

### What “needed” means (three layers)

1. **Coverage need:** every central testable unit has at least one grounded item.
2. **Evidence need:** enough responses to estimate mastery / ability at a stated precision for the active mode.
3. **Pipeline need:** cook ahead so the learner never waits (batch size). This is **not** the budget.

Today’s `REFILL_BATCH_SIZE = 5` is layer 3. The engine owns layers 1–2. Keep them separate forever.

---

## 2. What research says (synthesis + citations)

### 2.1 Classical test theory: length vs reliability

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Spearman–Brown prophecy formula (Spearman; Brown) | 1910 | [Wikipedia overview](https://en.wikipedia.org/wiki/Spearman%E2%80%93Brown_prediction_formula) | Reliability rises with parallel length; **diminishing returns**. Inverse form: items needed for target reliability. |
| MetricGate SB notes | n.d. | [metricgate.com/docs/spearman-brown-prophecy](https://metricgate.com/docs/spearman-brown-prophecy/) | Practical inverse `k = r*(1−r_xx)/(r_xx(1−r*))`; assumes parallel items. |

**Implication:** fixed “always 10 per page” is psychometric nonsense. Homogeneous single-trait tests need length for reliability; multi-concept study pages need **stratified coverage first**, then length *within* strand.

### 2.2 CAT: when enough is enough

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Weiss & Kingsbury (SE termination; cited widely) | 1984 | via [JCAT termination paper](https://jcatpub.net/index.php/jcat/article/download/16/3/110) | Stop when `SE(θ̂)` hits a precision target (variable length). |
| Choi, Grady, Dodd | 2010 | [PMC3028267](https://pmc.ncbi.nlm.nih.gov/articles/PMC3028267/) | SE rule + hybrid min/max item floors; avoid stopping on early luck. |
| Termination criteria review (JCAT) | 2013 | [doi:10.7333/jcat.v1i1.16](https://doi.org/10.7333/jcat.v1i1.16) | Longer helps, then diminishing returns; SE works if bank is informative. |
| IRT information identity | n.d. | [Cogn-IQ TIF](https://www.cogn-iq.org/learn/theory/test-information-function/) | `SE(θ) = 1/√I(θ)`; `SE≤0.30` ⇒ `I≥≈11.1`. |

**Implication:** **serve-time** stop criteria should be information-based (or mastery-based). **Cook-time** budget must still ensure the bank has enough informative, content-balanced items to *reach* that stop.

### 2.3 Knowledge tracing / mastery: evidence per skill

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Corbett & Anderson, Knowledge tracing | 1995 | [Springer](https://link.springer.com/doi/10.1007/BF01099821) | Mastery = latent skill state; tutor keeps assigning until P(mastered) is high. |
| BKT practice (threshold ~0.95) | n.d. | [Williams BKT note](https://www.cs.williams.edu/~iris/res/bkt/); [MetricGate](https://metricgate.com/docs/sequential-irt-knowledge-tracing/) | Operational stop: `P(L_t) ≥ 0.95` (sometimes 0.90–0.98). |
| Beck et al. / EDM mastery threshold analysis | 2013 | [EDM 2013 PDF](https://educationaldatamining.org/EDM2013/papers/rn_paper_08.pdf) | Threshold trades false mastery vs over-practice. |
| pyBKT | 2021 | [arXiv:2105.00385](https://arxiv.org/abs/2105.00385) | Worst-case mastery estimation improves with longer sequences; asymptote ~15 responses/skill in their sim. |
| Pelánek / Elo for student modeling | (common practice) | (Zivo already online-Elo in `calibration.py`) | Lightweight KT; evidence accumulates per concept rating. |

**Implication:** Learn mode budgets should be **per concept**, not per page word count alone. One strong item can start evidence; mastery may need more over time (spacing), not all at once on one page.

### 2.4 Question generation evaluation

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Fu et al., QGEval | 2024 | [ACL Anthology](https://aclanthology.org/2024.emnlp-main.658/); [arXiv](https://arxiv.org/abs/2406.05707) | Judge fluency, clarity, relevance, consistency, answerability; models often fail answerability. Not a “count” metric. |

**Implication:** Budget planner must not optimize raw count. Every planned slot needs a **grounded, answerable** aspect. Quality gates (verify/critic) remain mandatory; over-budget cook that fails gates is still under-covered.

### 2.5 Instructional design: dosing and spacing

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Lyle et al.; spaced retrieval in STEM | 2019–2024 | [Ed Psych Review 2019](https://link.springer.com/article/10.1007/s10648-019-09489-x); [STEM Ed 2024](https://link.springer.com/article/10.1186/s40594-024-00468-5) | Spacing beats massing; ~3 practice items per objective across quizzes is a common experimental dose. |
| Retrieval Practice / Spacing Guide | n.d. | [pdf.retrievalpractice.org](https://pdf.retrievalpractice.org/SpacingGuide.pdf) | Any spacing > none; more retrievals help, with diminishing returns. |
| Shrock & Coscarelli (cited in assessment practice) | 2007 | via [ResearchGate discussion](https://www.researchgate.net/post/How_to_Calculate_Determine_Number_of_Items_in_an_Examination_Set) | **4–6 items per objective** for reliable objective-level decisions. |
| Authentic Assessment Toolbox (domain sampling) | n.d. | [jonfmueller.com](https://jonfmueller.com/toolbox/tests/whatshouldiassess1.htm) | Broad standards may need 10–15 MCQs for domain sampling (high-stakes sampling, not page Learn). |

**Implication:**

- **Learn (first pass on a page):** ~**1** high-quality item per central objective (start the retrieval loop).
- **Test / mastery check:** ~**3** formative, or **4–6** if the page/objective is a high-stakes gate.
- Extra dose belongs in **spaced revisit**, not in stuffing one page session.

### 2.6 Multi-document / passage coverage

| Source | Year | Link | One-line takeaway |
|--------|------|------|-------------------|
| Bovair & Kieras, propositional analysis | (classic; ch. reprints) | [doi:10.4324/9781315099958-12](https://doi.org/10.4324/9781315099958-12) | Text = list of propositions; coverage measurable against that list. |
| Concept-map analysis of RC items | 2026 | [doi:10.1016/j.stueduc.2026.101599](https://doi.org/10.1016/j.stueduc.2026.101599) | Typical MCQs hit under 10% of an expert concept map; centrality matters more than raw count. |

**Implication:** Prefer **central concepts** over exhaustive proposition coverage. Budget to the concept graph spine, not every sentence.

### 2.7 Product norms (secondary)

| Product | Pattern | Link |
|---------|---------|------|
| Duolingo | ~up to ~17 items / short lesson | [duoplanet learning path](https://duoplanet.com/duolingo-learning-path/) |
| Khan Academy Mastery Challenge | **6** questions, **2 per skill × 3 skills** | [KA help](https://support.khanacademy.org/hc/en-us/articles/360037494231-What-are-Mastery-Challenges) |

Useful for **session soft caps**, not for page cook math on a dense PDF page.

---

## 3. Proposed model (final architecture)

### 3.1 First principles

A page is a **bundle of knowledge components (KCs)**. A question is an **instrument** that yields information about one or more KCs. Budget is:

> the smallest set of instruments that (a) covers central KCs and (b) supplies enough expected information for the active mode’s decision rule.

Two decision rules:

| Mode | Decision | Cook target | Serve stop (later) |
|------|----------|-------------|--------------------|
| **Learn** | “Did we present a revealing prompt for each central KC?” | Coverage | Optional: early exit if learner already high Elo on that KC |
| **Test** | “Can we estimate ability / mastery with SE ≤ δ?” | Coverage × evidence multiplier, or I-target allocation | Hybrid: `SE(θ)≤δ` **or** all central KCs probed, with min/max |

### 3.2 Signals (inputs)

| Signal | Source today / soon | Role |
|--------|---------------------|------|
| `words`, substantial paragraphs | page text (existing heuristic) | Density prior when units missing |
| `aspects[]` with labels/keys | page triage LLM + dedup | Candidate KCs |
| `centrality` ∈ {central, support, skip} | LLM-assist tag or heuristic | Weight `w_u` |
| `content_type` / `non_content` | triage + newspaper ad filter | Force `N=0` |
| `programmable` | triage | Side channel (coding bank), not MCQ count |
| Mode `learn` \| `test` | workspace | Multiplier `m` |
| Prior Elo per concept | `calibration` / concept projections | Optional downweight already-mastered KCs in Learn |
| Mean item information `Ĩ` | prior from calibrated bank (default constant) | Convert SE target → item count for Test |

### 3.3 Default formula (deterministic core)

Let units `U = {u}` after aspect dedup. Weight:

```
w_u = 1.0 if central
      0.5 if support
      0.0 if skip / ad / boilerplate
```

Mode multipliers (opinionated defaults):

```
m_learn = 1
m_test_formative = 3          # ~ spaced-retrieval experimental dose / lower bound of 4–6
m_test_high_stakes = 5        # mid of Shrock & Coscarelli 4–6
```

**Coverage term:**

```
N_cov = round( Σ_u w_u * m_mode )
```

**Information floor (Test, document / session trait, not every page):**

```
I* = 1 / SE_target²
# formative default: SE_target = 0.40 → I* = 6.25
# firmer:           SE_target = 0.30 → I* = 11.11
N_info = ceil( I* / Ĩ )
# cold-start Ĩ ≈ 0.20 → N_info ≈ 32 items across the Test session / doc slice
```

Page cook stays coverage-driven. The information floor binds when assembling a Test **session** (or short document), so a 2-concept page is not forced to 32 items.

**Combine:**

```
N_page = 0                              if non_content
       = clamp(N_cov, FLOOR, CEIL)     otherwise   # Learn and Test pages

N_doc_cov = Σ_p N_page(p)
N_doc_test = max(N_doc_cov, N_info)    # Test profile only; Learn uses N_doc_cov
```

**Defaults:**

| Constant | Value | Why |
|----------|-------|-----|
| `FLOOR` | `0` | Honest empty pages (open-world). |
| `CEIL_page` | `40` | Soft product ceiling; cut support weights before hitting. Dense pages can still be large via central units up to ceil. |
| `W_PER_UNIT` | `120` | Existing Zivo heuristic (~one idea / 120 words). |
| `SESSION_SOFT` | `20` | Duolingo/KA-scale session comfort; serve pacing only. |
| `SE_target` (Test) | `0.40` | Formative; `0.30` reserved for high-stakes Test profiles. |
| `Ĩ` | `0.20` | Conservative until real item info from Elo/IRT bank exists. |

**Density prior** (no LLM aspects yet), matching current fallback spirit:

```
U_hat = min( |substantial_paragraphs|, max(1, words // W_PER_UNIT) )  if words > 0 else 0
N_prior = U_hat * m_mode   # then same clamp
```

**Document:**

```
N_doc = Σ_p N_page(p) for p in selected cookable pages
```

**Confidence** (expose next to budget):

```
confidence =
  high   if LLM aspects + dedup + non-junk gate
  medium if heuristic density prior only
  low    if speculative pre-triage seed (today’s INITIAL_BATCH_SIZE path)
```

### 3.4 LLM-assist vs deterministic

| Step | Owner | Why |
|------|-------|-----|
| Junk / ad / non-content | Deterministic (+ existing newspaper filter / vision) | Must be cheap, stable, testable |
| Unit segmentation + centrality | LLM-assist, constrained by density prior | Humans/LLMs see “ideas”; numbers stay clamped |
| `N_page` / `N_doc` arithmetic | **Pure deterministic function** | Policy seam; unit-tested; no LLM in the formula |
| Cook batching | Deterministic (`REFILL_BATCH_SIZE`) | Throughput only |

LLM may propose aspects; the engine **never trusts** raw `question_budget` from the model without running it through the formula (or validating `|aspects|` against `N_cov` and density prior ± tolerance).

### 3.5 Floors, ceilings, confidence (product)

- **No artificial Learn floor of 5.** Current open-world `0` on cover pages stays.
- **Ceilings cut periphery first:** drop `w_u=0.5` units before reducing `m_test`.
- **Confidence** gates UI copy: low confidence → show “estimating…” not a fake Y.

---

## 4. Algorithm steps

```
input: document_id, page p, mode, optional prior concept Elo map

1. Load page text (chunks). If not ingested → defer (no budget write).
2. Non-content gate (empty / junk / newspaper ad filter / vision).
   → if reject: N_page=0, non_content=true, stop.
3. Extract units:
   a. Prefer triage aspects (deduped).
   b. Else density prior from words + substantial paragraphs.
4. Assign centrality (LLM tag or heuristic: first/main paras central; TOC/refs skip).
5. Compute N_cov from weights × mode multiplier → N_page (clamp).
6. Optional Learn downweight: if Elo(concept) already high and mode=learn,
   set w_u to 0.5 or 0 (skip re-cook); never below coverage of unseen central KCs.
7. Record confidence + rationale + unit list.
8. Persist page_coverage.question_budget = N_page, aspects aligned to units.
9. Document rollup: N_doc_cov = sum pages; if Test, N_doc = max(N_doc_cov, N_info).
10. Cook loop (existing pool):
    while generated < N_page and uncovered central units remain:
        enqueue batch of size min(REFILL_BATCH_SIZE, remaining)
        draft → verify → critic → assert
    stop cook when coverage_complete OR (test bank has ≥ N_page ready)
11. Serve loop (future hardening):
    Learn: present until page budget answered or learner skips page.
    Test: hybrid stop SE(θ)≤δ OR session items exhausted, with min items floor (e.g. 3).
```

### Cook stop criteria (replace “refill forever until magic number”)

Stop generating for page `p` when **any** of:

1. `generated_on_page >= N_page` and all central aspects marked asked/abandoned, or  
2. `non_content`, or  
3. remaining aspects exceed `MAX_ASPECT_ATTEMPTS` failures (existing escape hatch).

`READY_LOW_WATER` / `REFILL_BATCH_SIZE` only decide *when* to enqueue the next chunk, never *how many total*.

---

## 5. Fit to Zivo

### 5.1 What exists

| Piece | Role today |
|-------|------------|
| `page_triage_graph.py` | LLM/heuristic → `question_budget` + `aspects` |
| `_fallback_triage` | `words // 120` ∩ substantial paragraphs |
| `question_pool.py` | `get_question_budget`, rolling cook, low-water refill |
| `REFILL_BATCH_SIZE` / `MAX_GENERATE_BATCH_SIZE` | Pipeline chunk = 5 |
| `calibration.py` + `selection.py` | Online Elo; productive-struggle selection |
| Newspaper ad filter | Zero budget on ads/junk |
| UI | `question_budget` / `plan_budget` as Y |

### 5.2 What to add

| Artifact | Purpose |
|----------|---------|
| `app/services/question_budget.py` | Pure planner (policy seam); see stub |
| `page_coverage` fields | `budget_confidence`, `budget_mode`, `n_cov`, `n_info`, `budget_version` |
| Document rollup | `question_progress.document_budget` or derive on read |
| ETA | Triage job calls planner after aspects; generate jobs consume `N_page` only |
| API `learn-queue` | Expose `plan_budget`, `document_budget`, `session_soft` separately |

### 5.3 What to replace

| Mentality | Replacement |
|-----------|-------------|
| “Budget ≈ refill batch vibes” | Budget = planner; batch = pipe |
| Unchecked LLM `question_budget` | LLM proposes units; formula sets `N` |
| Single Y for everything | Page Y for cook/UI; session soft for pacing; doc sum for planning |
| Massing 4–6 items on every Learn page | Learn `m=1`; schedule extras via spaced revisit / Test |

### 5.4 Tables / intel touchpoints

- Persist plan on existing progress / `page_coverage` (no schema freeze break).
- Later: link aspects → `intel.concept`; mastery via `projection` (`student.ability` / per-concept Elo already nascent in pool jobs).
- Item difficulty projections feed `Ĩ` empirically over time (swap constant).

### 5.5 Policy seam

Per ADR 0004 and VISION (“metric stays swappable”): keep `plan_page_budget(...)` pure and versioned (`budget_version = "qb.budget.v1"`). Tomorrow: IRT info from real discriminations; same socket.

---

## 6. Failure modes

| Failure | Behavior |
|---------|----------|
| **Thin / cover / TOC page** | Junk gate + `N=0`, `non_content`. |
| **Ads / newspaper junk** | Existing `newspaper_page_verdict`; budget 0. |
| **Huge PDF page (400 “ideas”)** | Density prior + `CEIL_page`; centrality; never one ETA job of 400 (`MAX_GENERATE_BATCH_SIZE` stays small). |
| **Huge document** | Large `N_doc` OK for cook plan; UI uses page Y + session soft; eager triage lookahead stays bounded. |
| **Newspaper edition** | Per-page cook filter; doc budget = sum of cookable article pages only. Default open = Learn (§0); Test only via explicit CTA. |
| **LLM over-propose** | Clamp to prior ± tolerance; dedup aspects. |
| **LLM under-propose** | Prefer max(LLM units, density prior * m) for central text pages when confidence medium. |
| **All drafts fail verify/critic** | Existing `MAX_ASPECT_ATTEMPTS`; mark abandoned; do not inflate budget. |
| **Pre-ingest race** | Defer budget write (existing). |

---

## 7. Measurement: how we know budget is “right”

| Probe | Method | Pass signal |
|-------|--------|-------------|
| **Coverage** | Fraction of central aspects with ≥1 grounded assertion | → 1.0 on completed pages |
| **Gate yield** | Accepted drafts / attempted | Stable; low yield ⇒ budget too greedy or prompts weak |
| **Reliability proxy (Test)** | Empirically `1 − mean(SE²)` or Cronbach/SB on item sets | Approaches target for Test profiles |
| **Mastery calibration** | After Learn+spaced, P(correct) on held-out same-KC items | Matches Elo/BKT predictions |
| **Learner outcomes** | Time-to-page-complete, abandon rate, “too many/few” feedback | Abandon down; subjective “about right” up |
| **Diminishing returns** | Marginal info per extra item on page | Extra items after `N_info` add little SE reduction |
| **A/B** | `m_test` 2 vs 3 vs 5 on same pages | Pick via outcomes, not taste |

Instrument these in sim (`app/sim`) and production analytics; do not Goodhart a single frozen “ideal N.”

---

## 8. Open decisions (only blocking ones)

None are blocking for v1 of the engine. Defaults above are intentional:

1. Learn `m=1`, Test formative `m=3`, SE_target `0.40`, `CEIL_page=40`, `SESSION_SOFT=20`.
2. Page is atomic; document is sum.
3. Formula owns N; LLM owns unit labels/centrality suggestions.
4. Refill batch stays an ETA constant, not a product budget.

Revisit only when calibrated `Ĩ` and per-concept Elo coverage are rich enough to make `N_info` dominate `N_cov` on real banks.

---

## 9. Implementation sketch (minimal now)

Pure function module: `backend/app/services/question_budget.py`  
Tests: `backend/tests/unit/test_question_budget.py`, wire coverage in
`test_page_triage.py` / `test_learn_queue.py` / `test_question_budget_wire.py`

Wire-up (done for v1):

1. Call planner from `_finalize_triage` / triage success path.  
2. Store `budget_version` + confidence on `page_coverage`.  
3. Teach `learn-queue` to distinguish `plan_budget` vs `session_soft` (+ `document_budget`).  
4. Leave `REFILL_BATCH_SIZE` as pipe chunk only (comments clarify vs plan).

Still later: Test-mode multiplier at cook time, Elo downweight, richer `N_info` from bank.

---

## 10. Recommended default (plain math)

**Learn page:**

```
N_page = clamp( round(Σ_u w_u × 1) , 0, 40 )
```

**Test page (formative):**

```
N_page = clamp( round(Σ_u w_u × 3) , 0, 40 )
```

**Document / Test session:**

```
N_doc_cov = Σ_p N_page(p)
N_info    = ceil( (1 / SE²) / Ĩ )     # SE=0.40, Ĩ≈0.20 → ≈32
N_doc     = N_doc_cov                 # Learn
N_doc     = max(N_doc_cov, N_info)    # Test
```

**Unit density prior when aspects missing:** `U_hat = min(|substantial paras|, words/120)`.

Durable centerpiece: **coverage-weighted units × mode evidence, IRT information floor at document/session for Test, honest zeros, pipeline batching kept out of the formula.**
