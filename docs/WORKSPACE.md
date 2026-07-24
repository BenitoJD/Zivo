# Workspace

UI is implemented with **Mantine** in `frontend/app/workspace/` — no custom components. See [AGENTS.md](../AGENTS.md#frontend-ui).

## Learn mode (mobile-first)

- **MCQ** hero fills the viewport
- Meta bar: **Page N · Question X of Y** (Y = agent `question_budget` for that page)
- Budget planner (research-backed): [QUESTION_BUDGET_ENGINE.md](QUESTION_BUDGET_ENGINE.md); page vs document N; cook plan ≠ refill batch
- Parallel cook / zero-wait Learn: [MCQ_PARALLEL_COOK.md](MCQ_PARALLEL_COOK.md) — never full-screen wait if a question is ready
- Bottom tabs: MCQ · Source · Chat
- Source/Chat open as full-height sheets
- **Page = study unit** — triage agent sets how many questions the page supports (5–150)
- Rolling prefetch: 5 questions initially, +5 when the learner finishes question 3 (and every 3rd answer until budget/coverage)
- **Server resume** — `GET learn-queue` restores page, question number, and next assertion after reload
- **Page complete** — brief interstitial, then auto-advance to the next page in the selected range
- **Range complete** — after the last page in a range, choose the next page span from the same book (progress resets server-side)

## Desktop (`lg+`)

- 50/50 horizontal split: MCQ | Source+Chat stacked

## Test mode

- MCQ fullscreen; chat and file API return 403

## Routes

- `/workspace` — shell
- `/workspace/[artifactId]` — deep link
