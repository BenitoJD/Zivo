"""MCQ payloads shared by chat output and grading."""

from __future__ import annotations

import uuid

from typing import NoReturn

from pydantic import BaseModel, Field, field_validator

from app.engine_runtime import pick


def _invalid(message: str) -> NoReturn:
    raise ValueError(message)


class McqQuestion(BaseModel):
    question: str = Field(min_length=4, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0)
    explanation: str = Field(default="", max_length=4000)

    @field_validator("options")
    @classmethod
    def strip_options(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in filter(lambda option: option.strip(), value)]
        return pick(
            len(cleaned) < 2,
            lambda: _invalid("At least two non-empty options required"),
            lambda: cleaned,
        )

    @field_validator("correct_index")
    @classmethod
    def index_in_range(cls, value: int, info) -> int:
        options = info.data.get("options") or []
        return pick(
            bool(options) and value >= len(options),
            lambda: _invalid("correct_index out of range"),
            lambda: value,
        )


class McqGradeRequest(BaseModel):
    document_id: uuid.UUID
    question: str = Field(min_length=4, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=10)
    correct_index: int = Field(ge=0)
    selected_index: int = Field(ge=0)
    explanation: str = Field(default="", max_length=4000)


class McqGradeResponse(BaseModel):
    is_correct: bool
    correct_index: int
    selected_index: int
    feedback: str
