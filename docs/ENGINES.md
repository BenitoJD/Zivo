# Question Better engines

Index of durable policy engines (ADR 0004 seams). Budget plans N; Quality decides survivors; Selection picks next; Calibration updates ratings; the rest specialize cook/serve/grade hygiene.

**Scan rule:** DONE = doc + facade + live-path call (not compute-and-discard) + unit tests. PARTIAL = facade exists but a parallel ad-hoc path remains or outputs are not persisted/consumed.

| # | Engine | Doc | Facade | Live path | Tests | Status |
|---|--------|-----|--------|-----------|-------|--------|
| 1 | Question Budget | [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md) | `question_budget.py` | triage + pool | yes | DONE |
| 2 | Quality Evaluation | [QUALITY_EVALUATION_ENGINE.md](QUALITY_EVALUATION_ENGINE.md) | `quality_evaluation.py` | mcq cook gates | yes | DONE |
| 3 | Adaptive Selection | [ADAPTIVE_SELECTION_ENGINE.md](ADAPTIVE_SELECTION_ENGINE.md) | `adaptive_selection.py` | learn-queue next-Q | yes | DONE |
| 4 | Calibration | [CALIBRATION_ENGINE.md](CALIBRATION_ENGINE.md) | `calibration_engine.py` | grade + concept Elo | yes | DONE |
| 5 | KC / Coverage Label | [KC_COVERAGE_ENGINE.md](KC_COVERAGE_ENGINE.md) | `kc_coverage.py` | triage normalize + coverage complete | yes | DONE |
| 6 | Mastery / Evidence-Stop | [MASTERY_EVIDENCE_ENGINE.md](MASTERY_EVIDENCE_ENGINE.md) | `mastery_evidence.py` | grade → progress → learn-queue | yes | DONE |
| 7 | Question Graph / Lineage | [QUESTION_GRAPH_ENGINE.md](QUESTION_GRAPH_ENGINE.md) | `question_graph.py` | cook lineage write | yes | DONE |
| 8 | Misconception / Distractor | [MISCONCEPTION_DISTRACTOR_ENGINE.md](MISCONCEPTION_DISTRACTOR_ENGINE.md) | `misconception_distractor.py` | all quality cook paths | yes | DONE |
| 9 | Content Worthiness Gate | [CONTENT_WORTHINESS_ENGINE.md](CONTENT_WORTHINESS_ENGINE.md) | `content_worthiness.py` | empty PDF + newspaper + heuristic skip | yes | DONE |
| 10 | Grounding / Answerability | [GROUNDING_ANSWERABILITY_ENGINE.md](GROUNDING_ANSWERABILITY_ENGINE.md) | `grounding_answerability.py` | all quality cook paths | yes | DONE |
| 11 | Spaced Revisit | [SPACED_REVISIT_ENGINE.md](SPACED_REVISIT_ENGINE.md) | `spaced_revisit.py` | grade → progress due map | yes | DONE |
| 12 | Session Design | [SESSION_DESIGN_ENGINE.md](SESSION_DESIGN_ENGINE.md) | `session_design.py` | learn-queue session_soft | yes | DONE |
| 13 | Item Health / Bank Hygiene | [ITEM_HEALTH_ENGINE.md](ITEM_HEALTH_ENGINE.md) | `item_health.py` | retirement ETA | yes | DONE |

## Pipeline sketch

```
cook:  Worthiness → Budget → generate → Grounding + Distractor → Quality → Graph lineage → birth Calibration prior
serve: Session Design → Selection (reads Calibration + Graph + KC) → learner
grade: measurement → Calibration update → Mastery stop + Spaced revisit persisted
hygiene: Item Health / retirement (CTT)
```

Zero-wait refill (`REFILL_BATCH_SIZE`) stays outside Budget and Selection math.
