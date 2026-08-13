import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.services.minio import ensure_bucket

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        ensure_bucket()
    except Exception as exc:
        logger.exception("startup: object-storage bucket check failed: %s", exc)
    yield


app = FastAPI(
    title="Zivo Storage",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

_OUTAGE_SQLSTATE_CLASSES = {"08", "53", "57"}


def _is_db_outage(exc: SQLAlchemyError) -> bool:
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return bool(sqlstate) and sqlstate[:2] in _OUTAGE_SQLSTATE_CLASSES


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("database error: %s", exc)
    if _is_db_outage(exc):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong processing that request."},
    )


class HstsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if settings.is_production and request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
            )
        return response


if settings.is_production:
    app.add_middleware(HstsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id", "X-Zivo-Internal-Key"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id", "X-Zivo-Internal-Key"],
    )

app.include_router(api_router, prefix="/api")


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}
