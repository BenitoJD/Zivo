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
| 12 | Session Design | [SESSION_DESIGN_ENGINE.md](SESSION_DESIGN_ENGINE.md) | `session_design.py` | learn-queue `session_break` + background/newspaper cook | yes | DONE |
| 13 | Item Health / Bank Hygiene | [ITEM_HEALTH_ENGINE.md](ITEM_HEALTH_ENGINE.md) | `item_health.py` | retirement ETA (flag + retire) | yes | DONE |
| 14 | Practice Selection | (sibling of Adaptive Selection) | `practice_selection.py` | coding + system-design next (attempt bias on all next-pick paths) | yes | DONE |
| 15 | Tutor Retrieval | (this index) | `tutor_retrieval.py` | chat gate + RAG window + chunk rank | yes | DONE |
| 16 | SEO Gate | (this index) | `seo_gate.py` | seo cook usefulness + dedupe + presentation mix | yes | DONE |
| 17 | Open Response Measurement | (this index) | `open_response.py` | mains + interview + coding teach + SD heuristic + coding bank gate | yes | DONE |
| 18 | Aspect Discovery | (this index) | `aspect_discovery.py` | triage pick + speculative + next-unasked | yes | DONE |
| 19 | Learn Lesson | (this index) | `page_lessons.py` | page cook (before MCQ loop) → learn-queue `page_lesson` | yes | DONE |
| 20 | LLM Prose | [LLM_PROSE_ENGINE.md](LLM_PROSE_ENGINE.md) | `llm_prose_engine.py` | `llm_router` output boundary | yes | DONE |

## Pipeline sketch

```
cook:  Worthiness → Budget → Aspect Discovery pick → generate → Grounding + Distractor → Quality → Graph lineage → birth Calibration prior
serve: Session Design → Selection (reads Calibration + Graph + KC + Spaced due + Mastery stop) → learner
tutor: Tutor Retrieval (gate → window → rank) → LLM → LLM Prose sanitize
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
| RAG window readiness (stale window vs sticky flag) | Tutor Retrieval | `tutor_retrieval.evaluate_rag_window_ready` |
| Tutor chunk fetch strategy | Tutor Retrieval | `tutor_retrieval.plan_chunk_retrieval` |
| Semantic chat-cache reuse threshold | Tutor Retrieval | `tutor_retrieval.decide_cache_reuse` |
| Grade-tutor chunk top_n | Tutor Retrieval | `tutor_retrieval.grade_context_top_n` |
| Brainstorm skips vector RAG | Tutor Retrieval | `tutor_retrieval.decide_retrieval` (`reason=brainstorm`) |
| Brainstorm whole-source chunk sample | Tutor Retrieval | `tutor_retrieval.plan_brainstorm_sample` |
| Chat history compress | Tutor Retrieval | `tutor_retrieval.compress_chat_history` |
| Aspect cluster threshold / `dedupe_aspects` | Aspect Discovery | `aspect_discovery.dedupe_aspects` |
| Heuristic fallback triage density | Aspect Discovery | `aspect_discovery.heuristic_fallback_aspects` |
| Substantial paragraph density | Aspect Discovery | `aspect_discovery.substantial_paragraphs` |
| Aspect abandon after failed cooks | Aspect Discovery | `aspect_discovery.should_abandon_aspect` |
| MCQ stem similarity threshold | Quality Evaluation | `quality_evaluation.judge_mcq_similarity` |
| Best-of-N draft structural pick | Quality Evaluation | `quality_evaluation.pick_best_draft` |
| Critic sample-rate default | Quality Evaluation | `quality_evaluation.DEFAULT_CRITIC_SAMPLE_RATE` |
| Vision empty-page glance → skip reason | Content Worthiness | `content_worthiness.evaluate_vision_glance` (vision LLM stays plumbing) |
| Empty-page reselect streak | Content Worthiness | `content_worthiness.plan_empty_page_reselect` |
| Newspaper page structure (ad / masthead / low_signal) | Content Worthiness | `content_worthiness.evaluate_newspaper_structure` |
| Newspaper cook/skip (structure + exam relevance) | Content Worthiness | `content_worthiness.evaluate_newspaper_cook_gate` |
| Empty study-range reason | Content Worthiness | `content_worthiness.evaluate_empty_study_reason` |
| LLM triage honest-zero | Content Worthiness | `content_worthiness.evaluate_llm_triage_units` |
| Digest page worthiness | Content Worthiness | `content_worthiness.evaluate_digest_page_worthy` |
| Newspaper batch skip coverage stamp | Content Worthiness | `content_worthiness.plan_newspaper_batch_gate` |
| Newspaper naming confidence | Content Worthiness | `newspaper_naming.evaluate_naming_confidence` |
| Practice attempt bias (+unattempted / −done) | Practice Selection | `practice_selection.score_candidate` / `pick_next(attempted_ids=…)` |
| Coding heuristic teach-gap lesson | Open Response | `open_response.heuristic_coding_teach_gap` |
| System-design heuristic grade | Open Response | `open_response.heuristic_system_design_grade` |
| Coding bank structural persist gate | Open Response | `open_response.evaluate_coding_bank_item` |
| Interview coding pass/fail + degraded scores | Open Response | `open_response.evaluate_interview_coding_turn` / `degraded_interview_scores` |
| Coding reference verify + sample/hidden split | Open Response | `open_response.evaluate_coding_reference_verify` / `plan_coding_test_visibility` |
| Mains marks band + word target | Open Response | `open_response.plan_mains_attempt` |
| System-design LLM/heuristic grade merge | Open Response | `open_response.merge_system_design_grade` |
| Coding solve measurement persist | Open Response | `open_response.should_record_coding_solve` |
| Cross-doc MCQ reuse scope (`off`/`demo`/`all`) | Question Graph | `question_graph.plan_mcq_reuse` |
| Cook lineage edge confidence | Question Graph | `question_graph.lineage_confidence` |
| Focus / mastery diversify / spaced prefer | Adaptive Selection | `adaptive_selection.narrow_serve_pool` (via `select_next`) |
| Learner-facing selection labels | Adaptive Selection | `adaptive_selection.label_selection_reason` |
| Cook grounding fatality (page words + min score) | Grounding / Answerability | `grounding_answerability.evaluate_grounding_for_cook` |
| SEO near-dupe cosine | SEO Gate | `seo_gate.NEAR_DUPE_COSINE` (`seo_dedupe` plumbing) |
| SEO daily soft publish cap | SEO Gate | `seo_gate.evaluate_publish_cap` |
| SEO article format mix + CTA | SEO Gate | `seo_gate.plan_article_presentation` |
| SEO MCQ attach mix | SEO Gate | `seo_gate.plan_seo_mcq_attach` |
| SEO MCQ attach-ready floor | SEO Gate | `seo_gate.evaluate_seo_mcq_attach_ready` |
| Newspaper edition digest ranking | SEO Gate | `seo_gate.plan_newspaper_digest` |
| Newspaper digest source ready | SEO Gate | `seo_gate.evaluate_digest_source_ready` / `digest_skip_reason` |
| SEO embedding near-dupe | SEO Gate | `seo_gate.evaluate_embedding_near_dupe` |
| Newspaper SEO cook candidate | SEO Gate | `seo_gate.evaluate_newspaper_seo_candidate` |
| SEO system-design daily quota | SEO Gate | `seo_gate.plan_sd_daily_cook` |
| Pool refill / transition / periodic cadence | Session Design | `session_design.evaluate_serve_schedule` |
| Interview round plans by category | Session Design | `session_design.plan_interview_rounds` |
| Background-prep index/cook progress blend | Session Design | `session_design.evaluate_prep_progress` |
| Background-prep cook tick | Session Design | `session_design.evaluate_background_cook_tick` |
| Newspaper edition cook vs triage | Session Design | `session_design.evaluate_newspaper_edition_tick` |
| Learn page-complete / advance | Session Design | `session_design.evaluate_page_complete` |
| Newspaper learn-complete + catalog ready | Session Design | `session_design.evaluate_newspaper_learn_complete` / `evaluate_newspaper_catalog_ready` |
| Interview MCQ/coding question shape | Session Design | `session_design.plan_interview_question_shape` |
| Interview coding fallback bank pick | Session Design | `session_design.pick_interview_coding_fallback` |
| Document-complete / last-page gate | Session Design | `session_design.evaluate_document_complete` |
| Background-prep page ready | Session Design | `session_design.evaluate_page_prep_ready` |
| Newspaper learn-then-test cook target | Session Design | `session_design.plan_newspaper_cook_target` |
| Soft session break after N_session | Session Design | `session_design.evaluate_session_break` |
| Newspaper triage-complete UI signal | Session Design | `session_design.evaluate_newspaper_triage_complete` |
| Learn cook coverage close | Session Design | `session_design.evaluate_learn_cook_coverage_close` / `evaluate_empty_batch_coverage_close` |
| Auxiliary coding/debug cook spawn | Session Design | `session_design.evaluate_auxiliary_cook_spawn` / `alias_debuggable` |
| First-cook batch size | Session Design | `session_design.plan_first_cook_batch` / `evaluate_background_first_batch` |
| Transition next-page triage/cook | Session Design | `session_design.plan_transition_next` |
| Aspect-exhaustion coverage close | KC / Coverage Label | `kc_coverage.evaluate_aspect_exhaustion_close` |
| Serve-path coverage complete | KC / Coverage Label | `kc_coverage.evaluate_page_coverage_complete` |
| Persist coverage-complete stamp | KC / Coverage Label | `kc_coverage.should_persist_coverage_complete` |
| Aspect answered on grade | KC / Coverage Label | `kc_coverage.mark_aspects_answered` |
| Sequence vs N_page | Question Budget | `question_budget.exceeds_page_budget` |
| Serve generation/refill stop | Question Budget | `question_budget.evaluate_generation_stop` |
| Speculative pre-triage `N_page` | Question Budget | `question_budget.speculative_page_budget` |
| Serve-mode page budget replan | Question Budget | `question_budget.resolve_page_budget` |
| Practice-hub path mastery bands | Mastery / Evidence-Stop | `mastery_evidence.label_path_mastery` |
| SD path sample + next focus | Mastery / Evidence-Stop | `mastery_evidence.sample_path_mastery` / `plan_path_focus` |
| Learn-lesson central aspects | Learn Lesson | `page_lessons.plan_lesson_aspects` |

## Client-side seams (Offline Mode)

Offline Mode mirrors the verdict computation on the client so a learner can grade
with no network, but **no policy is forked to the client.** The local seam is a
pure index-compare, identical to the server's `grade_verdict`:

| Seam | Mirrors | Scope |
|------|---------|-------|
| Local study engine (`frontend/lib/offline/engine.ts`) | `mcq_graph.grade_verdict` (verdict only) + pre-baked `option_feedback` | Verdict + bundled feedback offline; the real Adaptive/Calibration/Mastery engines run on sync |

Pack build (server) and grade replay (server) are the authoritative paths; the
local engine is a throwaway projection whose grades are recomputed server-side
on reconnect. See [ADR 0006](adr/0006-offline-answer-keys.md).
