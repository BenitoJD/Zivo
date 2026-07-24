# MCQ parallel cook: zero-wait Learn

Jobs law for Learn: **hide latency**. If a question exists, the learner answers it.
Never full-screen spinner when `pool_available > 0` (or any unanswered card on the page).

Budget stop (`N_page`) stays sacred: cook toward the planner yield; refill batch size is
pipe only. See [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md).

## Research anchors (top 3)

1. **ReQUESTA** ([arXiv:2602.03704](https://arxiv.org/html/2602.03704v1)) — hybrid MCQ pipeline:
   plan once, then **parallel specialized generators** (factual / inferential / main idea).
   Takeaway: after a plan exists, item writing is embarrassingly parallel; serial end-to-end
   prompting is the wrong default.

2. **PipeInfer / async speculation** ([arXiv:2407.11798](https://arxiv.org/html/2407.11798v1)) —
   overlap draft work with verification on the critical path. Takeaway for us: while Q1
   quality-gates, **already draft the rest of the batch** (stage overlap), instead of
   waiting for a single mega-draft of N items before any gate runs.

3. **Streaming / progressive disclosure UX** ([AI/TLDR latency patterns](https://ai-tldr.dev/learn/building-ai-apps/ai-ux-patterns/designing-for-llm-latency/),
   [Brainy: Designing for AI latency](https://brainy.ink/paper/designing-for-ai-latency)) —
   time-to-first-*item* is perceived latency; background agents fill the rest. Takeaway:
   show the first ready MCQ immediately; background fill is invisible success.

### Also useful (not main levers)

| Source | One-line takeaway |
|--------|-------------------|
| [HiveMind](https://arxiv.org/html/2604.17111v1) | Admit LLM calls with a process-wide concurrency gate; respect RPM/TPM. |
| [Rate limits as distributed systems](https://tianpan.co/blog/2026-04-17-llm-rate-limits-distributed-systems-starvation) | Priority queues: interactive Q1 must not starve behind batch refill / SEO. |
| [SAGA work-stealing](https://arxiv.org/html/2605.00528) | Steal idle worker capacity; keep affinity when cache locality matters. |
| Speculative decoding (SPD / SpecPipe) | Token-level GPU trick; **not** our multi-question cook lever. |
| [KNIGHT](https://arxiv.org/abs/2602.20135) | Generate then filter; quality gates stay after draft, never skip. |

## Zivo stage graph (what can overlap)

```
ingest.page / RAG window ──┐
                           ├──► page_triage (plan N_page, aspects)
speculative aspects ───────┘         │
                                     ▼
                         ┌── draft Q1 ── gate Q1 ── persist ──► LEARN UNBLOCKS
                         │         │
                         │         └── (overlap) draft Q2..Qk
                         │                    │
                         │                    ▼
                         │              parallel gates (GENERATION_CONCURRENCY)
                         │                    │
                         └──────────────► persist + embed/coach enqueue
                                     │
                                     ▼
                         refill while pool < READY_LOW_WATER
                         until generated >= N_page (or coverage complete)
```

| Stage | Serial? | Overlap |
|-------|---------|---------|
| Read / RAG window | Per page ingest | Eager triage starts during ingest |
| Plan (triage) | One LLM/heuristic per page | Runs **parallel** with first batch via speculative aspects |
| Draft Q1 | One small draft call | Rest-of-batch draft starts **during** Q1 gate when `len(targets) > 1` |
| Gate Q1 | Serial + force critic | Unblocks Learn via `on_accept` + commit |
| Gate Q2..Qk | Parallel thread pool | Bounded by `ZIVO_GENERATION_CONCURRENCY` × `LLM_MAX_CONCURRENT` |
| Embed / coach | After accept | Async job; never blocks stem render |
| Next-page triage | Background | `EAGER_TRIAGE_LOOKAHEAD`, transition ratios |

## Priority and SLOs

| Work | Priority | SLO intent |
|------|----------|------------|
| `generate.questions` (triage + page_batch) | **HIGH** | Q1 wall-clock: draft + one gate, not full `N_page` |
| Ingest / embed / coding | MEDIUM | Must not jump ahead of empty-pool Learn |
| SEO cook / bulk | IO / lower urgency | Must not starve interactive CPU queue |

Background fill target: warm pool ≥ `READY_LOW_WATER` (default 8), batches of
`REFILL_BATCH_SIZE` (5), first job size `FIRST_QUESTION_BATCH_SIZE` (1).

## Concurrency knobs

| Knob | Default | Role |
|------|---------|------|
| `ETA_CPU_WORKER_MAX_CONCURRENCY` | 4 | Concurrent generate jobs per CPU worker |
| `ZIVO_GENERATION_CONCURRENCY` | 4 | Intra-batch parallel quality gates |
| `LLM_MAX_CONCURRENT` | 8 | Process-wide LLM semaphore (fan-out ceiling) |
| `ZIVO_READY_LOW_WATER` | 8 | When to enqueue refill |
| `ZIVO_PIPELINE_DRAFT_SPLIT` | 1 | Overlap rest-draft with Q1 gate (multi-target batches) |

**Newspaper vs PDF:** same cook graph. Newspaper pages are usually short (`N_page`
small); PDF long-tail pages need the warm pool + low-water more. Do not raise
batch size to chase PDF speed; raise overlap and priority instead.

Product of worker concurrency × gate concurrency should stay under provider RPM/TPM.

## UX contract (regression: 92% spinner with "2 ready")

Invariant:

```
if unansweredReady: never show full-screen GenerationStages wait
unansweredReady =
  current_assertion_id
  OR pool_available > 0
  OR questions_generated > questions_answered
```

Copy may say background writing; chrome must be the MCQ card. Progress ring at 92%
with "N ready" while blocking is a **bug**, not a status.

Proof surface: `frontend/app/(shell)/workspace/_components/McqPanels.tsx`,
`frontend/lib/learnStatus.ts`.

## Latency hypothesis

| Change | Expected effect |
|--------|-----------------|
| FE unansweredReady harden | Eliminates false full-screen wait when cards already exist |
| Pipeline draft split (rest during Q1 gate) | Cuts refill time-to-next by ~one gate RTT when batch > 1 |
| `generate.questions` HIGH | Protects Q1 under mixed CPU load |
| Parallel gates after Q1 (shipped) | Warm pool fills while learner studies Q1 |

Measure: time from Learn open → first stem visible; pool depth after 30s; never
block when learn-queue reports ready cards.
