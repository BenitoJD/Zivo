"""Shared pytest hooks for the library service.

Backend engines are imported as a library (this conftest puts ../backend on sys.path).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
_BACKEND = _REPO / "backend"


def _prepend(path: str) -> None:
    {True: lambda: sys.path.remove(path), False: lambda: None}[path in sys.path]()
    sys.path.insert(0, path)


_prepend(str(_ROOT))
_prepend(str(_BACKEND))
