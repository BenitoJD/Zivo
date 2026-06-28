"""Shared pytest hooks for the Zivo backend test suite."""

from __future__ import annotations

import pytest

# Register ETA handlers before any test module imports question_pool → jobs.
from app.main import app as _app  # noqa: F401
from app.services import prompts as _prompts


@pytest.fixture(autouse=True)
def _isolate_prompt_cache():
    """Clear the in-process prompt-template cache around every test.

    get_prompt() memoizes templates in a module-level dict (60s TTL). A test that
    calls it with a mock DB caches a mock value under a real prompt key, which then
    leaks into a later test that expects the real default. Clearing it per test
    keeps tests order-independent.
    """
    _prompts._prompt_template_cache.clear()
    yield
    _prompts._prompt_template_cache.clear()
