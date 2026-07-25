# Question Better engines

Index of durable policy engines (ADR 0004 seams). Budget plans N; Quality decides survivors; Selection picks next; Calibration updates ratings; the rest specialize cook/serve/grade hygiene.

**Scan rule:** DONE = doc + facade + live-path call (not compute-and-discard) + unit tests. PARTIAL = facade exists but a parallel ad-hoc path remains or outputs are not persisted/consumed.

| # | Engine | Doc | Facade | Live path | Tests | Status |
|---|--------|-----|--------|-----------|-------|--------|
| 1 | Question Budget | [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md) | `question_budget.py` | triage + pool | yes | DONE |
| 2 | Quality Evaluation | [QUALITY_EVALUATION_ENGINE.md](QUALITY_EVALUATION_ENGINE.md) | `quality_evaluation.py` | mcq cook gates | yes | DONE |
| 3 | Adaptive Selection | [ADAPTIVE_SELECTION_ENGINE.md](ADAPTIVE_SELECTION_ENGINE.md) | `adaptive_selection.py` | learn-queue next-Q | yes | DONE |
| 4 | Calibration | [CALIBRATION_ENGINE.md](CALIBRATION_ENGINE.md) | `calibration_engine.py` | grade + concept Elo + birth prior | yes | DONE |
| 5 | KC / Coverage Label | [KC_COVERAGE_ENGINE.md](KC_COVERAGE_ENGINE.md) | `kc_coverage.py` | triage normalize + coverage complete | yes | DONE |
| 6 | Mastery / Evidence-Stop | [MASTERY_EVIDENCE_ENGINE.md](MASTERY_EVIDENCE_ENGINE.md) | `mastery_evidence.py` | grade → progress → learn-queue | yes | DONE |
| 7 | Question Graph / Lineage | [QUESTION_GRAPH_ENGINE.md](QUESTION_GRAPH_ENGINE.md) | `question_graph.py` | cook lineage write | yes | DONE |
| 8 | Misconception / Distractor | [MISCONCEPTION_DISTRACTOR_ENGINE.md](MISCONCEPTION_DISTRACTOR_ENGINE.md) | `misconception_distractor.py` | all quality cook paths | yes | DONE |
| 9 | Content Worthiness Gate | [CONTENT_WORTHINESS_ENGINE.md](CONTENT_WORTHINESS_ENGINE.md) | `content_worthiness.py` | empty PDF + newspaper + junk skip (sole facade) | yes | DONE |
| 10 | Grounding / Answerability | [GROUNDING_ANSWERABILITY_ENGINE.md](GROUNDING_ANSWERABILITY_ENGINE.md) | `grounding_answerability.py` | all quality cook paths | yes | DONE |
| 11 | Spaced Revisit | [SPACED_REVISIT_ENGINE.md](SPACED_REVISIT_ENGINE.md) | `spaced_revisit.py` | grade → ease/reps + due map → Selection prefer | yes | DONE |
| 12 | Session Design | [SESSION_DESIGN_ENGINE.md](SESSION_DESIGN_ENGINE.md) | `session_design.py` | learn-queue `session_break` | yes | DONE |
| 13 | Item Health / Bank Hygiene | [ITEM_HEALTH_ENGINE.md](ITEM_HEALTH_ENGINE.md) | `item_health.py` | retirement ETA (flag + retire) | yes | DONE |
| 14 | Practice Selection | (sibling of Adaptive Selection) | `practice_selection.py` | coding + system-design next (attempt bias on all next-pick paths) | yes | DONE |
| 15 | Tutor Retrieval | (this index) | `tutor_retrieval.py` | chat gate + RAG window + chunk rank | yes | DONE |
| 16 | SEO Gate | (this index) | `seo_gate.py` | seo cook usefulness + dedupe (`NEAR_DUPE_COSINE`) | yes | DONE |
| 17 | Open Response Measurement | (this index) | `open_response.py` | mains + interview + coding teach + SD heuristic + coding bank gate | yes | DONE |
| 18 | Aspect Discovery | (this index) | `aspect_discovery.py` | triage pick + speculative + next-unasked | yes | DONE |

## Pipeline sketch

```
cook:  Worthiness → Budget → Aspect Discovery pick → generate → Grounding + Distractor → Quality → Graph lineage → birth Calibration prior
serve: Session Design → Selection (reads Calibration + Graph + KC + Spaced due + Mastery stop) → learner
tutor: Tutor Retrieval (gate → window → rank) → LLM
grade: measurement (MCQ or Open Response) → Calibration update → Mastery stop + Spaced revisit persisted
hygiene: Item Health / retirement (CTT)
practice hubs: Practice Selection (overlap×difficulty)
seo: SEO Gate (usefulness + dedupe) → writer
```

Zero-wait refill (`REFILL_BATCH_SIZE`) stays outside Budget math. Pool low-water /
transition / periodic refill live in **Session Design** (`evaluate_serve_schedule`).
Speculative pre-triage `N_page` lives in **Question Budget** (`speculative_page_budget`).

**Holy grail:** decisions live in engines; orchestration must not own if-else policy. See [AGENTS.md](../AGENTS.md#holy-grail-engines).

## Sub-policies folded into existing engines

| Sub-policy | Parent | Facade entry |
|------------|--------|--------------|
| `birth_prior_v1` | Calibration | `calibration_engine.birth_difficulty_prior` |
| RAG window size / look-ahead | Tutor Retrieval | `tutor_retrieval.plan_rag_window` |
| Cross-encoder rank vs truncate | Tutor Retrieval | `tutor_retrieval.finish_ranked_chunks` (calls `rerank` plumbing) |
| Learn pin `current_page` before RAG window | Tutor Retrieval | `tutor_retrieval.decide_page_pin` |
| Semantic chat-cache reuse threshold | Tutor Retrieval | `tutor_retrieval.decide_cache_reuse` |
| Grade-tutor chunk top_n | Tutor Retrieval | `tutor_retrieval.grade_context_top_n` |
| Brainstorm skips vector RAG | Tutor Retrieval | `tutor_retrieval.decide_retrieval` (`reason=brainstorm`) |
| Brainstorm whole-source chunk sample | Tutor Retrieval | `tutor_retrieval.plan_brainstorm_sample` |
| Chat history compress | Tutor Retrieval | `tutor_retrieval.compress_chat_history` |
| Aspect cluster threshold / `dedupe_aspects` | Aspect Discovery | `aspect_discovery.dedupe_aspects` |
| Heuristic fallback triage density | Aspect Discovery | `aspect_discovery.heuristic_fallback_aspects` |
| Aspect abandon after failed cooks | Aspect Discovery | `aspect_discovery.should_abandon_aspect` |
| MCQ stem similarity threshold | Quality Evaluation | `quality_evaluation.judge_mcq_similarity` |
| Best-of-N draft structural pick | Quality Evaluation | `quality_evaluation.pick_best_draft` |
| Critic sample-rate default | Quality Evaluation | `quality_evaluation.DEFAULT_CRITIC_SAMPLE_RATE` |
| Vision empty-page glance → skip reason | Content Worthiness | `content_worthiness.evaluate_vision_glance` (vision LLM stays plumbing) |
| Empty-page reselect streak | Content Worthiness | `content_worthiness.plan_empty_page_reselect` |
| Newspaper naming confidence | Content Worthiness | `newspaper_naming.evaluate_naming_confidence` |
| Practice attempt bias (+unattempted / −done) | Practice Selection | `practice_selection.score_candidate` / `pick_next(attempted_ids=…)` |
| Coding heuristic teach-gap lesson | Open Response | `open_response.heuristic_coding_teach_gap` |
| System-design heuristic grade | Open Response | `open_response.heuristic_system_design_grade` |
| Coding bank structural persist gate | Open Response | `open_response.evaluate_coding_bank_item` |
| Cross-doc MCQ reuse scope (`off`/`demo`/`all`) | Question Graph | `question_graph.plan_mcq_reuse` |
| Focus / mastery diversify / spaced prefer | Adaptive Selection | `adaptive_selection.narrow_serve_pool` (via `select_next`) |
| Cook grounding fatality (page words + min score) | Grounding / Answerability | `grounding_answerability.evaluate_grounding_for_cook` |
| SEO near-dupe cosine | SEO Gate | `seo_gate.NEAR_DUPE_COSINE` (`seo_dedupe` plumbing) |
| SEO daily soft publish cap | SEO Gate | `seo_gate.evaluate_publish_cap` |
| Pool refill / transition / periodic cadence | Session Design | `session_design.evaluate_serve_schedule` |
| Interview round plans by category | Session Design | `session_design.plan_interview_rounds` |
| Speculative pre-triage `N_page` | Question Budget | `question_budget.speculative_page_budget` |
| Practice-hub path mastery bands | Mastery / Evidence-Stop | `mastery_evidence.label_path_mastery` |
