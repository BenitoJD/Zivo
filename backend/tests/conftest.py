"""Shared pytest hooks for the Zivo backend test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app.engine_runtime import pick

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _svc in ("practice", "content", "study", "library", "admin", "workers"):
    _path = str(_REPO_ROOT / _svc)
    pick(_path not in sys.path, lambda p=_path: sys.path.append(p), lambda: None)

# Register ETA handlers before any test module imports question_pool → jobs.
import app.eta  # noqa: F401
from app.repositories import intel as _intel
from app.services import prompts as _prompts


@pytest.fixture(autouse=True)
def _isolate_module_caches():
    """Clear in-process memo caches around every test so order can't leak state.

    Several modules memoize lookups in module-level dicts. A unit test that calls
    one with a mock DB caches a mock value under a real key, which then poisons a
    later test that expects the real value:
      - get_prompt() -> prompts._prompt_template_cache (templates, 60s TTL)
      - concept_id()/source ids -> intel._concept_id_store / _source_id_store
        (a poisoned concept id breaks the calibration write path downstream).
    Clearing them per test keeps the suite order-independent.
    """
    caches = (
        _prompts._prompt_template_cache,
        _intel._concept_id_store,
        _intel._source_id_store,
    )
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()
