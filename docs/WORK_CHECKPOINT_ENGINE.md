# Work Checkpoint Engine

Crash-resilient progress + resume for every long-running LLM job, so a pod
death never re-bills completed work.

## Policy (the engine)

`backend/app/services/work_checkpoint.py` — pure facade (`qb.workckpt.v1`):

- **`plan(items)`** — a `WorkCheckpoint` is the ordered list of item keys a
  job must complete plus the subset already done.
- **`mark_done(ckpt, *keys)`** — returns the next checkpoint (immutable).
- **`progress` / `remaining`** — 0..100 progress and the todo list, pure
  policy (no caller if-else).
- **`resume_work(db, items, is_done)`** — load-or-plan: if the item plan
  changed (source edited) only keys still present survive; `is_done(key)`
  re-verifies an item's artifact still exists, so a torn write (crash
  between item completion and checkpoint save) drops back into `remaining`.

## Storage

The checkpoint lives on the ETA job row's `result` JSONB under
`"work_checkpoint"` — no new table. The ETA lifecycle already persists
`result`, so a checkpoint survives worker restart, lease expiry, and reaper
reclaim for free. `current_job_id()` reads the job from ETA context; tests
can pass `job_id` explicitly.

## Live path

- Audiobook: `build_audiobook` plans one checkpoint item per MP3 chunk,
  `mark_done` after each render, and `resume_work` skips chunks whose MP3
  exists (torn uploads re-render).
- (Extend the same seam to notes/flashcards/mains/quiz/interview as they
  adopt per-item rendering.)

## Why the job row

The ETA reaper already requeues orphaned jobs with their `result` intact;
storing the checkpoint there means resume works with zero new infrastructure
and zero migration. The engine keeps the key layout and merge rules in one
place (ADR 0004 seam) so callers only say *what* the units of work are.
