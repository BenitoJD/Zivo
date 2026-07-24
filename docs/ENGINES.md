# Question Better engines

Index of durable policy engines (ADR 0004 seams). Budget plans N; Quality decides survivors; Selection picks next; Calibration updates ratings; the rest specialize cook/serve/grade hygiene.

| # | Engine | Doc | Facade | Status |
|---|--------|-----|--------|--------|
| 1 | Question Budget | [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md) | `question_budget.py` | wired |
| 2 | Quality Evaluation | [QUALITY_EVALUATION_ENGINE.md](QUALITY_EVALUATION_ENGINE.md) | `quality_evaluation.py` | wired |
| 3 | Adaptive Selection | [ADAPTIVE_SELECTION_ENGINE.md](ADAPTIVE_SELECTION_ENGINE.md) | `adaptive_selection.py` | wired |
| 4 | Calibration | [CALIBRATION_ENGINE.md](CALIBRATION_ENGINE.md) | `calibration_engine.py` | wired |
| 5 | KC / Coverage Label | [KC_COVERAGE_ENGINE.md](KC_COVERAGE_ENGINE.md) | `kc_coverage.py` | wired |
| 6 | Mastery / Evidence-Stop | [MASTERY_EVIDENCE_ENGINE.md](MASTERY_EVIDENCE_ENGINE.md) | `mastery_evidence.py` | wired |
| 7 | Question Graph / Lineage | [QUESTION_GRAPH_ENGINE.md](QUESTION_GRAPH_ENGINE.md) | `question_graph.py` | wired |
| 8 | Misconception / Distractor | [MISCONCEPTION_DISTRACTOR_ENGINE.md](MISCONCEPTION_DISTRACTOR_ENGINE.md) | `misconception_distractor.py` | wired |
| 9 | Content Worthiness Gate | [CONTENT_WORTHINESS_ENGINE.md](CONTENT_WORTHINESS_ENGINE.md) | `content_worthiness.py` | wired |
| 10 | Grounding / Answerability | [GROUNDING_ANSWERABILITY_ENGINE.md](GROUNDING_ANSWERABILITY_ENGINE.md) | `grounding_answerability.py` | wired |
| 11 | Spaced Revisit | [SPACED_REVISIT_ENGINE.md](SPACED_REVISIT_ENGINE.md) | `spaced_revisit.py` | wired |
| 12 | Session Design | [SESSION_DESIGN_ENGINE.md](SESSION_DESIGN_ENGINE.md) | `session_design.py` | wired |
| 13 | Item Health / Bank Hygiene | [ITEM_HEALTH_ENGINE.md](ITEM_HEALTH_ENGINE.md) | `item_health.py` | wired |

## Pipeline sketch

```
cook:  Worthiness → Budget → generate → Grounding + Distractor → Quality → Graph lineage → birth Calibration prior
serve: Session Design → Selection (reads Calibration + Graph + KC) → learner
grade: measurement → Calibration update → Mastery stop hint → Spaced revisit plan
hygiene: Item Health / retirement (CTT)
```

Zero-wait refill (`REFILL_BATCH_SIZE`) stays outside Budget and Selection math.
