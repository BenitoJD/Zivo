"""Shared pytest hooks for citepage backend tests."""

from __future__ import annotations

# Register ETA handlers before any test module imports question_pool → jobs.
from app.main import app as _app  # noqa: F401
