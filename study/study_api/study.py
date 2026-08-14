"""Study artifacts — notes, cheat sheets, flashcards (Scribely-style, additive to MCQ)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.services import brainstorm as brainstorm_service
from app.services import saved_notes as saved_notes_service
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest_session import guest_session_for_read
from app.services.rate_limit import rate_limit_dependency
from app.services.document_access import require_document, require_ready_document
from app.services import interview as interview_service
from app.services import mains as mains_service
from app.services.memory_palace import ensure_palace
from app.services.quiz import ensure_quiz, load_quiz
from app.services.study_artifacts import NOTE_KINDS, ensure_flashcards, ensure_notes

router = APIRouter()


def _owner(user: Account | None) -> uuid.UUID | None:
    """The account_id to scope notes/ideas by.

    Authenticated callers own their notes (and only see/delete their own); guests
    pass None, which the service maps to the legacy NULL bucket — shared, as
    before, because guests have no stable identity to scope by.
    """
    return user.id if user else None




@router.get("/{artifact_id}/notes", dependencies=[Depends(rate_limit_dependency)])
def get_notes(
    artifact_id: uuid.UUID,
    kind: str = Query("notes"),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Structured study notes (kind='notes') or a one-page cheat sheet (kind='cheatsheet').

    Generates in the background on first request. Returns
    {status: indexing|generating|ready|failed, kind, content}.
    """
    if kind not in NOTE_KINDS:
        kind = "notes"
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "kind": kind, "content": ""}
    state = ensure_notes(db, artifact_id, kind)
    return {"status": state["status"], "kind": kind, "content": state["content"]}


@router.get("/{artifact_id}/flashcards", dependencies=[Depends(rate_limit_dependency)])
def get_flashcards(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Active-recall flashcard deck. Generates in the background on first request.

    Returns {status: indexing|generating|ready|failed, cards: [{front,back,kind}]}.
    """
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "cards": []}
    state = ensure_flashcards(db, artifact_id)
    return {"status": state["status"], "cards": state["cards"]}


@router.get("/{artifact_id}/memory-palace", dependencies=[Depends(rate_limit_dependency)])
def get_memory_palace(
    artifact_id: uuid.UUID,
    setting: str = Query(""),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """A memory-palace journey for the source (Magnetic Memory Method).

    Generates in the background on first request; passing a `setting` (a place the learner
    knows well) regenerates the journey there. Returns
    {status: indexing|generating|ready|failed, setting, palace: {setting,intro,stations:[…]}}.
    """
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "setting": "", "palace": None}
    state = ensure_palace(db, artifact_id, setting=setting or None)
    return {"status": state["status"], "setting": state["setting"], "palace": state["palace"]}


# --------------------------------------------------- saved notes (Read / Study Buddy)
class SaveNoteIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    quote: str | None = Field(default=None, max_length=4000)


@router.get("/{artifact_id}/saved-notes")
def get_saved_notes(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """All notes the learner saved while reading this source (newest first)."""
    require_document(db, artifact_id, user, guest_id)
    return {"notes": saved_notes_service.list_notes(db, artifact_id, account_id=_owner(user), guest_id=guest_id)}


@router.post("/{artifact_id}/saved-notes", dependencies=[Depends(require_csrf_or_guest)])
def create_saved_note(
    artifact_id: uuid.UUID,
    body: SaveNoteIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Save an answer (and the passage it came from) as a note linked to this source."""
    require_document(db, artifact_id, user, guest_id)
    return saved_notes_service.add_note(
        db, artifact_id, content=body.content, quote=body.quote, account_id=_owner(user), guest_id=guest_id
    )


# --------------------------------------------------- brainstorm mode (kept ideas)
class SaveIdeaIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    angle: str = Field(default="", max_length=120)
    parent_id: uuid.UUID | None = None


@router.get("/{artifact_id}/brainstorm-ideas")
def get_brainstorm_ideas(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Ideas kept from brainstorming this source, as a tree.

    One nested shape serves both renderings: the mind map draws it directly, the
    idea board flattens it. Returns {tree: [{id,text,angle,children:[…]}]}.
    """
    require_document(db, artifact_id, user, guest_id)
    ideas = brainstorm_service.list_ideas(db, artifact_id, account_id=_owner(user), guest_id=guest_id)
    return {"tree": brainstorm_service.build_tree(ideas)}


@router.post("/{artifact_id}/brainstorm-ideas", dependencies=[Depends(require_csrf_or_guest)])
def create_brainstorm_idea(
    artifact_id: uuid.UUID,
    body: SaveIdeaIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Keep one idea. `parent_id` branches it off an existing idea (a mind-map edge)."""
    require_document(db, artifact_id, user, guest_id)
    try:
        return brainstorm_service.add_idea(
            db,
            artifact_id,
            idea_text=body.text,
            angle=body.angle,
            parent_id=body.parent_id,
            account_id=_owner(user),
            guest_id=guest_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/{artifact_id}/brainstorm-ideas/{idea_id}",
    status_code=204,
    dependencies=[Depends(require_csrf_or_guest)],
)
def delete_brainstorm_idea(
    artifact_id: uuid.UUID,
    idea_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    """Delete an idea and everything branched off it."""
    require_document(db, artifact_id, user, guest_id)
    brainstorm_service.delete_idea(db, artifact_id, idea_id, account_id=_owner(user), guest_id=guest_id)


@router.get("/{artifact_id}/brainstorm-ideas/export.md")
def export_brainstorm_ideas(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> Response:
    """The idea tree as nested markdown — the download target for "Export"."""
    doc = require_document(db, artifact_id, user, guest_id)
    ideas = brainstorm_service.list_ideas(db, artifact_id, account_id=_owner(user), guest_id=guest_id)
    title = (doc.filename or "").rsplit(".", 1)[0]
    body = brainstorm_service.to_markdown(title, ideas)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="brainstorm.md"'},
    )


# --------------------------------------------------- interview mode (mock interview)
class InterviewStartIn(BaseModel):
    category: str = Field(min_length=1, max_length=40)


class InterviewAnswerIn(BaseModel):
    # Typed rounds send text; MCQ rounds send the selected option index; coding rounds send
    # an object {source, language_id}.
    answer: str | int | dict = Field(default="")


@router.get("/{artifact_id}/interview")
def get_interview(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Current mock-interview state (setup | in_progress | complete). The pending question
    is returned with its answer key stripped; a `report` is included once complete."""
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "categories": interview_service.CATEGORY_META}
    return interview_service.load_interview(
        db,
        artifact_id,
        learner_key=interview_service.resolve_interview_learner_key(
            user.id if user else None, guest_id
        ),
    )


@router.post(
    "/{artifact_id}/interview/start",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def start_interview(
    artifact_id: uuid.UUID,
    body: InterviewStartIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Begin a mock interview for the chosen company category (asks the first question)."""
    require_ready_document(db, artifact_id, user, guest_id)
    learner_key = interview_service.resolve_interview_learner_key(
        user.id if user else None, guest_id
    )
    return await interview_service.start_interview(
        db, artifact_id, body.category, learner_key=learner_key
    )


@router.post(
    "/{artifact_id}/interview/answer",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def answer_interview(
    artifact_id: uuid.UUID,
    body: InterviewAnswerIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Evaluate the answer to the current question and advance to the next one."""
    require_document(db, artifact_id, user, guest_id)
    learner_key = interview_service.resolve_interview_learner_key(
        user.id if user else None, guest_id
    )
    try:
        return await interview_service.submit_answer(
            db, artifact_id, body.answer, learner_key=learner_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{artifact_id}/interview/reset", dependencies=[Depends(require_csrf_or_guest)])
def reset_interview(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Discard the current interview so a new category can be picked."""
    require_document(db, artifact_id, user, guest_id)
    learner_key = interview_service.resolve_interview_learner_key(
        user.id if user else None, guest_id
    )
    return interview_service.reset_interview(db, artifact_id, learner_key=learner_key)


# --------------------------------------------------- mains mode (descriptive answer grading)
class MainsStartIn(BaseModel):
    strictness: str = Field(default="coaching")  # exam | coaching | gentle
    marks_max: int = Field(default=10, ge=1, le=20)


class MainsAnswerIn(BaseModel):
    # Typed answers send text; handwritten answers upload a photo (as a source) and
    # send its document id, which the vision LLM reads on the worker.
    text: str = Field(default="", max_length=50000)
    image_document_id: uuid.UUID | None = None


@router.get("/{artifact_id}/mains")
def get_mains(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Current Mains state — poll while status is generating/grading.

    Returns {status: missing|indexing|generating|awaiting_answer|grading|ready|failed,
    question, directive, marks_max, strictness, input_kind, answer, result}. The marking
    scheme is hidden until `result` (which carries the revealed scheme_hits)."""
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "question": "", "marks_max": 10, "result": None}
    return mains_service.load_mains(db, artifact_id)


@router.post(
    "/{artifact_id}/mains/start",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def start_mains(
    artifact_id: uuid.UUID,
    body: MainsStartIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Generate a fresh descriptive question + hidden marking scheme (runs on the io worker)."""
    require_ready_document(db, artifact_id, user, guest_id)
    return mains_service.start_mains(
        db, artifact_id, strictness=body.strictness, marks_max=body.marks_max
    )


@router.post(
    "/{artifact_id}/mains/answer",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def answer_mains(
    artifact_id: uuid.UUID,
    body: MainsAnswerIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Submit the answer (typed and/or an uploaded photo) and enqueue examiner grading."""
    require_document(db, artifact_id, user, guest_id)
    # The answer photo is a separately-supplied document id — access-check it here (the
    # io worker's OCR has no user/guest context), else a caller could OCR/read any
    # image document cross-tenant (IDOR).
    if body.image_document_id is not None:
        require_document(db, body.image_document_id, user, guest_id)
    try:
        return mains_service.submit_mains_answer(
            db, artifact_id, text_answer=body.text, image_document_id=body.image_document_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


class RunCodeIn(BaseModel):
    source: str = Field(default="", max_length=50000)
    language_id: int = 71
    stdin: str = Field(default="", max_length=20000)


@router.get("/{artifact_id}/interview/languages")
def interview_languages(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Languages the coding editor can offer (Judge0 id → label)."""
    require_document(db, artifact_id, user, guest_id)
    from app.services.code_execution import LANGUAGES

    return {"languages": [{"id": k, "label": v} for k, v in LANGUAGES.items()]}


@router.post(
    "/{artifact_id}/interview/run-code",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def run_code_endpoint(
    artifact_id: uuid.UUID,
    body: RunCodeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """One-off compile+run of the editor's code against optional stdin (the 'Run' button)."""
    require_document(db, artifact_id, user, guest_id)
    from app.services.code_execution import run_code

    try:
        return await run_code(body.source, body.language_id, body.stdin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# --------------------------------------------------- resume suite (ATS / optimize / build)
class OptimizeIn(BaseModel):
    job_description: str = Field(default="", max_length=20000)


class BuildResumeIn(BaseModel):
    data: dict = Field(default_factory=dict)
    template: str = "ats"


@router.get("/{artifact_id}/resume/ats")
async def get_resume_ats(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """ATS score + checklist + strengths/improvements + a structured resume (cached)."""
    from app.services import resume as resume_service

    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "analysis": {}, "review_requested": False}
    return await resume_service.ensure_ats(db, artifact_id)


@router.post(
    "/{artifact_id}/resume/optimize",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def optimize_resume(
    artifact_id: uuid.UUID,
    body: OptimizeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Rewrite bullets ATS-friendly, optionally aligned to a target job description."""
    from app.services import resume as resume_service

    require_ready_document(db, artifact_id, user, guest_id)
    return await resume_service.optimize(db, artifact_id, body.job_description)


@router.post("/{artifact_id}/resume/request-review", dependencies=[Depends(require_csrf_or_guest)])
def request_resume_review(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Flag this resume for expert (manager) review."""
    from app.services import resume as resume_service

    require_document(db, artifact_id, user, guest_id)
    return resume_service.request_review(db, artifact_id)


@router.post("/{artifact_id}/resume/build.docx", dependencies=[Depends(require_csrf_or_guest)])
def build_resume_docx_endpoint(
    artifact_id: uuid.UUID,
    body: BuildResumeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    """Download the built resume as a .docx (template: 'ats' plain, or 'modern')."""
    from app.services.resume_export import build_resume_docx

    require_document(db, artifact_id, user, guest_id)
    template = "modern" if body.template == "modern" else "ats"
    data = build_resume_docx(body.data or {}, template=template)
    name = (body.data or {}).get("name") or "resume"
    fname = f"{str(name)[:50]}-{template}.docx".replace('"', "")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _parse_types(types: str) -> list[str]:
    return [t.strip() for t in (types or "").split(",") if t.strip()]


@router.get("/{artifact_id}/quiz", dependencies=[Depends(rate_limit_dependency)])
def get_quiz(
    artifact_id: uuid.UUID,
    types: str = Query("mcq"),
    count: int = Query(10),
    difficulty: str = Query("mixed"),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Generate a quiz/worksheet of the chosen question types (Question Generator).

    Regenerates in the background when the config (types/count/difficulty) changes.
    Returns {status: indexing|generating|ready|failed, config, questions:[…]} with the
    answer key included (the client hides answers for the student view).
    """
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "config": "", "questions": []}
    state = ensure_quiz(db, artifact_id, types=_parse_types(types), count=count, difficulty=difficulty)
    return {"status": state["status"], "config": state["config"], "questions": state["questions"]}


@router.get("/{artifact_id}/quiz/export.docx")
def export_quiz_docx(
    artifact_id: uuid.UUID,
    answers: int = Query(0),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    """Download the generated quiz as a .docx worksheet (answers=1 → teacher answer key)."""
    doc = require_document(db, artifact_id, user, guest_id)
    state = load_quiz(db, artifact_id)
    if state["status"] != "ready" or not state["questions"]:
        raise HTTPException(status_code=409, detail="Quiz not ready")

    from app.services.quiz_export import build_quiz_docx

    title = (doc.filename or "Quiz").rsplit(".", 1)[0]
    data = build_quiz_docx(title=title, questions=state["questions"], with_answers=bool(answers))
    suffix = "answer-key" if answers else "worksheet"
    fname = f"{title[:60]}-{suffix}.docx".replace('"', "")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.delete(
    "/{artifact_id}/saved-notes/{note_id}",
    status_code=204,
    dependencies=[Depends(require_csrf_or_guest)],
)
def delete_saved_note(
    artifact_id: uuid.UUID,
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    """Delete one saved note."""
    require_document(db, artifact_id, user, guest_id)
    saved_notes_service.delete_note(db, artifact_id, note_id, account_id=_owner(user), guest_id=guest_id)
