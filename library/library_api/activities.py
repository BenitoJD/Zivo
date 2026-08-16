"""User-visible job status (intel.activity)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session

from app.services.document_access import require_document
from app.db import SessionLocal, get_db
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.models import Account
from app.repositories.intel import get_activity
from app.services.auth import get_optional_user
from app.services.guest_session import guest_session_for_read
from app.services.presence import evaluate_presence

router = APIRouter()


def _raise_http(status: int, detail: str) -> None:
    raise HTTPException(status_code=status, detail=detail)


def _iso(value):
    return pick(bool(value), lambda: value.isoformat(), lambda: None)


def _activity_stats(row: dict) -> dict:
    stats = row.get("stats") or {}
    parsed = pick(isinstance(stats, str), lambda: json.loads(stats), lambda: stats)
    return pick(isinstance(parsed, dict), lambda: parsed, lambda: {})


def _require_activity_access(
    db: Session,
    row: dict,
    user: Account | None,
    guest_id: str | None,
) -> dict:
    """Gate activity status behind document access when stats carry an artifact_id."""
    stats = _activity_stats(row)
    artifact_raw = stats.get("artifact_id") or stats.get("document_id")
    pick(
        bool(artifact_raw),
        lambda: require_document(db, uuid.UUID(str(artifact_raw)), user, guest_id),
        lambda: pick(
            not user and not guest_id,
            lambda: _raise_http(401, "Authentication required"),
            lambda: None,
        ),
    )
    return stats


@router.get("/{activity_id}")
def get_job_status(
    activity_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    row = get_activity(db, activity_id)
    apply(
        evaluate_presence(row).action,
        {
            "missing": lambda: _raise_http(404, "Activity not found"),
            "empty": lambda: _raise_http(404, "Activity not found"),
            "ok": lambda: None,
        },
    )
    stats = _require_activity_access(db, row, user, guest_id)
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "stats": stats,
        "error_summary": row.get("error_summary"),
        "agent": row.get("agent"),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


@router.get("/{activity_id}/stream")
async def stream_activity(
    activity_id: uuid.UUID,
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    async def gen():
        import asyncio

        done = [False]
        tick = [0]
        while not done[0] and tick[0] < 120:
            tick[0] += 1
            db = SessionLocal()
            try:
                row = get_activity(db, activity_id)

                def _missing() -> list[dict]:
                    done[0] = True
                    return [{"event": "error", "data": json.dumps({"detail": "not found"})}]

                def _ok() -> list[dict]:
                    try:
                        stats = _require_activity_access(db, row, user, guest_id)
                    except HTTPException as exc:
                        done[0] = True
                        return [
                            {
                                "event": "error",
                                "data": json.dumps({"detail": exc.detail}),
                            }
                        ]
                    payload = {"status": row["status"], "stats": stats}
                    events = [{"event": "progress", "data": json.dumps(payload, default=str)}]
                    return apply(
                        first_match(
                            (
                                Rule(
                                    when=(Pred("terminal", "truthy"),),
                                    action="done",
                                ),
                                Rule(when=(), action="continue"),
                            ),
                            {
                                "terminal": row["status"]
                                in ("succeeded", "failed", "cancelled"),
                            },
                        ).action,
                        {
                            "done": lambda: (
                                done.__setitem__(0, True)
                                or events
                                + [{"event": "done", "data": json.dumps(payload, default=str)}]
                            ),
                            "continue": lambda: events,
                        },
                    )

                events = apply(
                    evaluate_presence(row).action,
                    {
                        "missing": _missing,
                        "empty": _missing,
                        "ok": _ok,
                    },
                )
                for ev in events:
                    yield ev
            finally:
                db.close()

            async def _idle() -> None:
                return None

            await pick(done[0], _idle, lambda: asyncio.sleep(1))

    return EventSourceResponse(gen())
