import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.engine_runtime import choose, pick

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="Zivo Auth",
    version="0.1.0",
    docs_url=choose(settings.is_production, None, "/docs"),
    redoc_url=choose(settings.is_production, None, "/redoc"),
    openapi_url=choose(settings.is_production, None, "/openapi.json"),
)

_OUTAGE_SQLSTATE_CLASSES = {"08", "53", "57"}


def _is_db_outage(exc: SQLAlchemyError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return pick(
        isinstance(exc, (OperationalError, InterfaceError)),
        lambda: True,
        lambda: bool(sqlstate) and sqlstate[:2] in _OUTAGE_SQLSTATE_CLASSES,
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("database error: %s", exc)
    return pick(
        _is_db_outage(exc),
        lambda: JSONResponse(status_code=503, content={"detail": "Database unavailable"}),
        lambda: JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong processing that request."},
        ),
    )


class HstsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        pick(
            settings.is_production and request.url.scheme == "https",
            lambda: response.headers.__setitem__(
                "Strict-Transport-Security",
                f"max-age={settings.hsts_max_age_seconds}; includeSubDomains",
            ),
            lambda: None,
        )
        return response


def _prod_middleware() -> None:
    app.add_middleware(HstsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )


def _dev_middleware() -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )


pick(settings.is_production, _prod_middleware, _dev_middleware)

app.include_router(api_router, prefix="/api")


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}
