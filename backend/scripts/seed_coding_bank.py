"""Seed the curated coding problem bank (classic starter set).

Usage:
  cd backend && python -m scripts.seed_coding_bank
  # or: ./scripts/dev.sh db migrate  then call POST /api/coding/admin/seed as admin
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.engine_runtime import pick

BACKEND_ROOT = Path(__file__).resolve().parents[1]
pick(str(BACKEND_ROOT) not in sys.path, lambda: sys.path.insert(0, str(BACKEND_ROOT)), lambda: None)

from app.db import SessionLocal  # noqa: E402
from app.services.coding_curation import seed_starter_bank  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        result = seed_starter_bank(db)
        db.commit()
        print(
            f"coding bank seed OK — created={result['created']} "
            f"updated={result['updated']} total={result['total']}"
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(f"coding bank seed FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _cli() -> None:
    raise SystemExit(main())


pick(__name__ == "__main__", _cli, lambda: None)
