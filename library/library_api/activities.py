"""User-visible job status (intel.activity)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session

from app.services.document_access import require_document
from app.db import SessionLocal, get_db
from app.models import Account
from app.repositories.intel import get_activity
from app.services.auth import get_optional_user
from app.services.guest_session import guest_session_for_read

router = APIRouter()


def _activity_stats(row: dict) -> dict:
    stats = row.get("stats") or {}
    if isinstance(stats, str):
        stats = json.loads(stats)
    return stats if isinstance(stats, dict) else {}


def _require_activity_access(
    db: Session,
    row: dict,
    user: Account | None,
    guest_id: str | None,
) -> dict:
    """Gate activity status behind document access when stats carry an artifact_id."""
    stats = _activity_stats(row)
    artifact_raw = stats.get("artifact_id") or stats.get("document_id")
    if artifact_raw:
        require_document(db, uuid.UUID(str(artifact_raw)), user, guest_id)
    elif not user and not guest_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return stats


@router.get("/{activity_id}")
def get_job_status(
    activity_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    row = get_activity(db, activity_id)
    if not row:
        raise HTTPException(status_code=404, detail="Activity not found")
    stats = _require_activity_access(db, row, user, guest_id)
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "stats": stats,
        "error_summary": row.get("error_summary"),
        "agent": row.get("agent"),
        "started_at": row.get("started_at").isoformat() if row.get("started_at") else None,
        "finished_at": row.get("finished_at").isoformat() if row.get("finished_at") else None,
    }


@router.get("/{activity_id}/stream")
async def stream_activity(
    activity_id: uuid.UUID,
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    async def gen():
        import asyncio

        for _ in range(120):
            db = SessionLocal()
            try:
                row = get_activity(db, activity_id)
                if not row:
                    yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                    return
                try:
                    stats = _require_activity_access(db, row, user, guest_id)
                except HTTPException as exc:
                    yield {
                        "event": "error",
                        "data": json.dumps({"detail": exc.detail}),
                    }
                    return
                payload = {"status": row["status"], "stats": stats}
                yield {"event": "progress", "data": json.dumps(payload, default=str)}
                if row["status"] in ("succeeded", "failed", "cancelled"):
                    yield {"event": "done", "data": json.dumps(payload, default=str)}
                    return
            finally:
                db.close()
            await asyncio.sleep(1)

    return EventSourceResponse(gen())
