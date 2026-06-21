"""MCQ payloads shared by chat output and grading."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


class McqQuestion(BaseModel):
    question: str = Field(min_length=4, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0)
    explanation: str = Field(default="", max_length=4000)

    @field_validator("options")
    @classmethod
    def strip_options(cls, value: list[str]) -> list[str]:
        cleaned = [o.strip() for o in value if o.strip()]
        if len(cleaned) < 2:
            raise ValueError("At least two non-empty options required")
        return cleaned

    @field_validator("correct_index")
    @classmethod
    def index_in_range(cls, value: int, info) -> int:
        options = info.data.get("options") or []
        if options and value >= len(options):
            raise ValueError("correct_index out of range")
        return value


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
