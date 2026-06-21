"""User-visible job status (intel.activity)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.repositories.intel import get_activity

router = APIRouter()


@router.get("/{activity_id}")
def get_job_status(activity_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = get_activity(db, activity_id)
    if not row:
        raise HTTPException(status_code=404, detail="Activity not found")
    stats = row.get("stats") or {}
    if isinstance(stats, str):
        stats = json.loads(stats)
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
async def stream_activity(activity_id: uuid.UUID, db: Session = Depends(get_db)):
    async def gen():
        import asyncio

        for _ in range(120):
            row = get_activity(db, activity_id)
            if not row:
                yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                return
            stats = row.get("stats") or {}
            payload = {"status": row["status"], "stats": stats}
            yield {"event": "progress", "data": json.dumps(payload, default=str)}
            if row["status"] in ("succeeded", "failed", "cancelled"):
                yield {"event": "done", "data": json.dumps(payload, default=str)}
                return
            await asyncio.sleep(1)

    return EventSourceResponse(gen())
