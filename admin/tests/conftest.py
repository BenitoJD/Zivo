"""Shared pytest hooks for the admin service.

Backend engines are imported as a library (this conftest puts ../backend on sys.path).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
_BACKEND = _REPO / "backend"
for path in (str(_BACKEND), str(_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
sys.path.remove(str(_BACKEND))
sys.path.insert(0, str(_BACKEND))
