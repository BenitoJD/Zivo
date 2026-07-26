# Debug diagnostics

Diagnostic reasoning scenarios: case file + stepped MCQs (root cause, fix approach, verify). Learners read artifacts; they do not write code.

## Format

- Assertion payload: `qb.debug.v1`
- Type concept: `/vocab/assertion/question.debug`
- Metric: `/vocab/metric/debug.understood`
- Facets: `qb.debug_assertion_facets`
- Cook jobs: `qb.debug_cook_job`

## Cook pipeline

1. Intake: learner paste, admin author, or auto from `debuggable` page triage
2. `generate.debug` ETA runs `debug_generation.run_cook_job`
3. Internal sandbox QA for code-grounded drafts (cook-time only)
4. Review: `draft` → `pending_review` → `approved` + `published`
5. Serve: `/api/debug` public bank, `/practice/debug` UI

## API

- `POST /api/debug/cook` — start private cook
- `GET /api/debug/cook/{id}` — job status
- `POST /api/debug/cook/{id}/submit-to-library` — learner promotion
- `GET /api/debug` — published scenarios
- `GET /api/debug/{id}` — play scenario (sanitized)
- `POST /api/debug/{id}/grade-step` — grade one MCQ step
- `POST /api/debug/{id}/reflect/stream` — process coaching
- `/api/debug/admin/*` — curate and review

## UI

- `/practice/debug` — public bank
- `/workspace/debug` — admin bank + cook entry
- `/workspace/debug/material` — learner cook intake
