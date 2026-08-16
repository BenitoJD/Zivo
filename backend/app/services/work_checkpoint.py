"""Work Checkpoint Engine — crash-resilient progress + resume for LLM jobs.

Design: docs/WORK_CHECKPOINT_ENGINE.md
Version: qb.workckpt.v1

Every long-running LLM job (audiobook, notes, flashcards, mains, quiz,
interview, generation) pays LLM cost per item. If the pod dies mid-job, the
ETA retry currently redoes work from zero — wasted tokens. This engine gives
any job a tiny, uniform checkpoint seam (ADR 0004):

  * ``WorkCheckpoint`` — a frozen plan of the items a job must complete
    (each item identified by a stable key) plus the set already done.
  * ``load`` / ``save`` — persisted on the job row's ``result`` JSONB under
    ``"work_checkpoint"``, so a reclaim/retry resumes exactly where the last
    attempt stopped. Torn writes are handled by the caller re-verifying item
    existence (e.g. the object is in MinIO) before trusting ``done``.
  * pure progress math — ``progress_percent`` / ``remaining`` are engine
    policy, not caller if-else.

Storage is deliberately the job row (no new table): the ETA lifecycle already
persists result, so a checkpoint survives worker restarts and reclaims for
free. The engine owns the key layout and the merge rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import uuid

from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.eta import context as eta_context
from app.models import Job

CHECKPOINT_VERSION = "qb.workckpt.v1"
_RESULT_KEY = "work_checkpoint"


@dataclass(frozen=True)
class WorkCheckpoint:
    """A job's unit-of-work plan + completion set.

    ``items`` is the full ordered list of item keys this job must complete.
    ``done`` is the subset already finished (by key). ``version`` pins the
    engine policy so a schema change in how checkpoints are shaped never
    silently reinterprets an old checkpoint.
    """

    items: tuple[str, ...] = ()
    done: frozenset[str] = frozenset()
    version: str = CHECKPOINT_VERSION

    @property
    def remaining(self) -> tuple[str, ...]:
        return tuple(filter(lambda k: k not in self.done, self.items))

    @property
    def progress(self) -> int:
        """0..100 — pure policy: share of completed items."""
        return pick(
            not self.items,
            lambda: 100,
            lambda: max(0, min(100, int(100 * len(self.done) / len(self.items)))),
        )


def plan(items: Sequence[str]) -> WorkCheckpoint:
    """Build a checkpoint from the ordered item keys the job must complete."""
    return WorkCheckpoint(items=tuple(str(k) for k in items))


def mark_done(checkpoint: WorkCheckpoint, *keys: str) -> WorkCheckpoint:
    """Return a new checkpoint with the given item keys marked complete.

    Keys not in the plan are ignored — ``done`` is always a subset of ``items``.
    """
    known = set(checkpoint.items)
    return WorkCheckpoint(
        items=checkpoint.items,
        done=frozenset(checkpoint.done) | (frozenset(str(k) for k in keys) & known),
        version=checkpoint.version,
    )


def to_dict(checkpoint: WorkCheckpoint) -> dict[str, Any]:
    return {
        "version": checkpoint.version,
        "items": list(checkpoint.items),
        "done": sorted(checkpoint.done),
    }


def from_dict(raw: Any) -> WorkCheckpoint:
    def _parse() -> WorkCheckpoint:
        raw_items = raw.get("items")
        return pick(
            not isinstance(raw_items, list),
            WorkCheckpoint,
            lambda: WorkCheckpoint(
                items=tuple(str(k) for k in filter(None, raw_items)),
                done=pick(
                    isinstance(raw.get("done"), list),
                    lambda: frozenset(str(k) for k in raw.get("done")),
                    frozenset,
                ),
                version=str(raw.get("version") or CHECKPOINT_VERSION),
            ),
        )

    return pick(not isinstance(raw, dict), WorkCheckpoint, _parse)


def current_job_id() -> uuid.UUID | None:
    """The ETA job id this code is running under (None off the job path)."""
    jid = eta_context.get_current_job_id()
    return pick(jid is not None, lambda: uuid.UUID(str(jid)), lambda: None)


def load(db: Session, job_id: uuid.UUID | None = None) -> WorkCheckpoint:
    """Load the checkpoint for a job (empty plan when none exists)."""
    jid = job_id or current_job_id()

    def _row() -> WorkCheckpoint:
        row = db.get(Job, jid)
        return pick(
            not row or not row.result,
            WorkCheckpoint,
            lambda: from_dict((row.result or {}).get(_RESULT_KEY)),
        )

    return pick(jid is None, WorkCheckpoint, _row)


def save(db: Session, checkpoint: WorkCheckpoint, job_id: uuid.UUID | None = None) -> None:
    """Persist the checkpoint on the job row (survives restart/reclaim)."""
    jid = job_id or current_job_id()

    def _save() -> None:
        row = db.get(Job, jid)

        def _write() -> None:
            result = dict(row.result or {})
            result[_RESULT_KEY] = to_dict(checkpoint)
            row.result = result
            db.commit()

        pick(not row, lambda: None, _write)

    pick(jid is None, lambda: None, _save)


def resume_work(
    db: Session,
    *,
    items: Sequence[str],
    is_done: Any = None,
    job_id: uuid.UUID | None = None,
) -> WorkCheckpoint:
    """Load-or-plan a checkpoint, re-verifying done items that no longer hold.

    ``is_done`` is an optional callable(item_key) -> bool that re-checks an
    item's artifact still exists (e.g. the MP3 is in MinIO). Items marked done
    but whose artifact vanished (torn write between item completion and
    checkpoint save) are dropped back into ``remaining``.
    """
    ckpt = load(db, job_id=job_id)
    new_items = tuple(str(k) for k in items)

    def _merge() -> WorkCheckpoint:
        new_set = set(new_items)
        kept_done = frozenset(filter(lambda k: k in new_set, ckpt.done))
        merged = WorkCheckpoint(items=new_items, done=kept_done)
        return pick(
            is_done is not None,
            lambda: WorkCheckpoint(
                items=merged.items,
                done=frozenset(filter(is_done, merged.done)),
            ),
            lambda: merged,
        )

    return pick(not ckpt.items, lambda: plan(new_items), _merge)
