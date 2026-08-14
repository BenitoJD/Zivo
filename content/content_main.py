import logging

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import health
from content_api import newspaper_admin, seo_learn
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="Zivo Content",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
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
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id"],
    )

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(seo_learn.router, prefix="/learn", tags=["learn-blog"])
api_router.include_router(newspaper_admin.router, prefix="/newspaper", tags=["newspaper-admin"])
app.include_router(api_router, prefix="/api")


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}
