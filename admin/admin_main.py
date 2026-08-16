import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from admin_api import debug, models
from app.api import health
from app.config import get_settings
from app.db import SessionLocal
from app.engine_runtime import choose, pick
from app.services.llm_registry import bootstrap_llm_registry_from_env

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        with SessionLocal() as db:
            bootstrap_llm_registry_from_env(db)
    except Exception as exc:
        logger.exception("startup: LLM registry bootstrap failed: %s", exc)
    yield


app = FastAPI(
    title="Zivo Admin",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=choose(settings.is_production, None, "/docs"),
    redoc_url=choose(settings.is_production, None, "/redoc"),
    openapi_url=choose(settings.is_production, None, "/openapi.json"),
)

_OUTAGE_SQLSTATE_CLASSES = {"08", "53", "57"}


def _is_db_outage(exc: SQLAlchemyError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return choose(
        isinstance(exc, (OperationalError, InterfaceError)),
        True,
        bool(sqlstate) and sqlstate[:2] in _OUTAGE_SQLSTATE_CLASSES,
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

        def _set_hsts() -> None:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
            )

        pick(
            settings.is_production and request.url.scheme == "https",
            _set_hsts,
            lambda: None,
        )
        return response


def _add_prod_middleware() -> None:
    app.add_middleware(HstsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id"],
    )


def _add_dev_middleware() -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id"],
    )


pick(settings.is_production, _add_prod_middleware, _add_dev_middleware)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(debug.router, prefix="/debug", tags=["debug"])
app.include_router(api_router, prefix="/api")


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}
