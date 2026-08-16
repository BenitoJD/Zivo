"""Shared pytest hooks for the study service.

Backend engines are imported as a library (this conftest puts ../backend on sys.path).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
_BACKEND = _REPO / "backend"


def _front(path: str) -> None:
    try:
        sys.path.remove(path)
    except ValueError:
        pass
    sys.path.insert(0, path)


_front(str(_ROOT))
_front(str(_BACKEND))
