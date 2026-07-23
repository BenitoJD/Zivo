"""System Design mastery API — path, recommend, sessions, submit."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.rate_limit import rate_limit_dependency
from app.services import system_design as sd

router = APIRouter()


def _subject(
    user: Account | None, guest_id: str | None
) -> tuple[uuid.UUID | None, str | None]:
    if user is not None:
        return user.id, None
    if not guest_id:
        raise HTTPException(status_code=401, detail="Guest session required")
    return None, guest_id


class DesignIn(BaseModel):
    requirements: str = Field(default="", max_length=12000)
    apis: str = Field(default="", max_length=12000)
    data: str = Field(default="", max_length=12000)
    scale: str = Field(default="", max_length=12000)
    blocks: list[str] = Field(default_factory=list, max_length=24)


class StartSessionIn(BaseModel):
    problem_id: uuid.UUID


@router.get("/path", dependencies=[Depends(rate_limit_dependency)])
def get_path(
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    return sd.build_path(db, account_id, gid)


@router.get("/recommended", dependencies=[Depends(rate_limit_dependency)])
def get_recommended(
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    active = sd.active_session(db, account_id, gid)
    problem = sd.recommend_problem(db, account_id, gid)
    path = sd.build_path(db, account_id, gid)
    return {
        "problem": problem,
        "active_session": active,
        "focus_key": path.get("focus_key"),
        "focus_title": path.get("focus_title"),
        "building_blocks": sd.BUILDING_BLOCKS,
    }


@router.get("/problems", dependencies=[Depends(rate_limit_dependency)])
def list_problems(
    difficulty: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return {"items": sd.list_problems(db, difficulty=difficulty)}


@router.get("/problems/{problem_id}", dependencies=[Depends(rate_limit_dependency)])
def get_problem(
    problem_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    row = sd.get_problem(db, problem_id, include_reference=False)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("/sessions", dependencies=[Depends(rate_limit_dependency), Depends(require_csrf_or_guest)])
def start_session(
    body: StartSessionIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    try:
        return sd.start_session(db, body.problem_id, account_id, gid)
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sessions/active", dependencies=[Depends(rate_limit_dependency)])
def get_active(
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    sess = sd.active_session(db, account_id, gid)
    return {"session": sess}


@router.get("/sessions/{session_id}", dependencies=[Depends(rate_limit_dependency)])
def get_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    sess = sd.get_session(db, session_id, account_id, gid)
    if not sess:
        raise HTTPException(status_code=404, detail="Not found")
    return sess


@router.post(
    "/sessions/{session_id}/design",
    dependencies=[Depends(rate_limit_dependency), Depends(require_csrf_or_guest)],
)
def save_design(
    session_id: uuid.UUID,
    body: DesignIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    try:
        return sd.save_design(db, session_id, account_id, gid, body.model_dump())
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/sessions/{session_id}/submit",
    dependencies=[Depends(rate_limit_dependency), Depends(require_csrf_or_guest)],
)
async def submit_session(
    session_id: uuid.UUID,
    body: DesignIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    account_id, gid = _subject(user, guest_id)
    try:
        return await sd.submit_and_grade(db, session_id, account_id, gid, body.model_dump())
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
