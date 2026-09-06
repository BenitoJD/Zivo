"""Quiz share API — create, share, take, review MCQ quiz sets. Policy: qb.quiz_share.v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services import quiz_share
from app.services.auth import require_csrf_or_guest
from app.services.http_outcome import evaluate_http_outcome
from app.services.llm_router import LlmDisabledError
from app.services.rate_limit import rate_limit_dependency

router = APIRouter(tags=["quiz-share"])

_POLICY = {"policy": quiz_share.QUIZ_SHARE_POLICY}


def _raise(exc: BaseException) -> None:
    raise exc


def _http(action: str, detail: str) -> None:
    _raise(HTTPException(status_code=evaluate_http_outcome(action).status, detail=detail))


def _present(result: dict | None) -> dict:
    apply(
        first_match(
            (
                Rule(when=(Pred("missing", "truthy"),), action="missing"),
                Rule(when=(), action="ok"),
            ),
            {"missing": result is None},
        ).action,
        {
            "missing": lambda: _http("missing", "Quiz not found"),
            "ok": lambda: None,
        },
    )
    return result


def _require_quiz_enabled() -> None:
    pick(
        get_settings().quiz_enabled,
        lambda: None,
        lambda: _http("deny", "Quiz feature is disabled"),
    )


class CreateQuizIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    creator_name: str = Field(default="Anonymous", max_length=100)
    questions: list[dict] = Field(min_length=1)


class SubmitAttemptIn(BaseModel):
    taker_name: str = Field(default="Anonymous", max_length=100)
    answers: list[int]


class GenerateQuestionsIn(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    count: int = Field(default=5, ge=1, le=20)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")


class FixQuestionsIn(BaseModel):
    questions: list[dict]


@router.post("/quiz/sets", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
def create_quiz(body: CreateQuizIn, db: Session = Depends(get_db)):
    return quiz_share.create_quiz_set(
        db,
        title=body.title,
        description=body.description,
        creator_name=body.creator_name,
        questions=body.questions,
    )


@router.get("/quiz/sets/{slug}")
def get_quiz(slug: str, db: Session = Depends(get_db)):
    return _present(quiz_share.get_quiz_set(db, slug))


@router.get("/quiz/sets/{slug}/manage")
def get_quiz_manage(slug: str, db: Session = Depends(get_db)):
    result = _present(quiz_share.get_quiz_set(db, slug, include_answers=True))
    return {**result, "results": quiz_share.get_creator_results(db, slug), **_POLICY}


@router.post("/quiz/sets/{slug}/submit", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
def submit_attempt(slug: str, body: SubmitAttemptIn, db: Session = Depends(get_db)):
    return _present(
        quiz_share.submit_attempt(db, slug, taker_name=body.taker_name, answers=body.answers)
    )


@router.post("/quiz/generate", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def generate_questions(body: GenerateQuestionsIn, db: Session = Depends(get_db)):
    _require_quiz_enabled()
    try:
        questions = await quiz_share.generate_questions_llm(
            db, topic=body.topic, count=body.count, difficulty=body.difficulty
        )
    except LlmDisabledError as exc:
        _raise(HTTPException(status_code=503, detail=str(exc)))
    return {"questions": questions, **_POLICY}


@router.post("/quiz/fix", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def fix_questions(body: FixQuestionsIn, db: Session = Depends(get_db)):
    _require_quiz_enabled()
    try:
        fixed = await quiz_share.fix_questions_llm(db, questions=body.questions)
    except LlmDisabledError as exc:
        _raise(HTTPException(status_code=503, detail=str(exc)))
    return {"questions": fixed, **_POLICY}
