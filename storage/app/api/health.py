from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness() -> dict[str, str | bool]:
    from app.db import check_database

    try:
        check_database()
        return {"status": "ok", "database": True}
    except Exception:
        return {"status": "degraded", "database": False}
