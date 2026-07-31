import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

import app.eta  # noqa: F401 — register ETA handlers
from app.api.router import api_router
from app.config import get_settings
from app.db import SessionLocal
from app.eta.scheduler_runtime import eta_scheduler_service
from app.services.embed import set_active_embed_model
from app.services.llm_registry import bootstrap_llm_registry_from_env, resolve_embedding_model
from app.services.storage import ensure_bucket

logger = logging.getLogger(__name__)
settings = get_settings()


def _warmup_retrieval_models() -> None:
    """Load the embedding model before serving chat (reranker stays lazy to save RAM)."""
    from app.services.embed import embed_query

    embed_query("warmup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        ensure_bucket()
    except Exception as exc:
        logger.exception("startup: object-storage bucket check failed: %s", exc)
    try:
        with SessionLocal() as db:
            bootstrap_llm_registry_from_env(db)
            try:
                embed = resolve_embedding_model(db)
                set_active_embed_model(embed.record.litellm_model)
                await asyncio.to_thread(_warmup_retrieval_models)
                logger.info("startup: retrieval models warmed up")
            except Exception as exc:
                logger.exception("startup: embedding model resolution failed: %s", exc)
    except Exception as exc:
        logger.exception("startup: LLM registry bootstrap failed: %s", exc)
    eta_scheduler_service.start()
    yield
    eta_scheduler_service.stop()


# Hide /docs, /redoc, /openapi.json in production — the ingress routes "/" with
# Prefix, so without this the full API surface (including admin paths) would be
# publicly discoverable at api.zivo.fyi. Dev keeps the docs for local iteration.
app = FastAPI(
    title="Zivo API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)


# SQLSTATE class prefixes for transient outage / infra conditions. Anything here
# is genuinely "the DB is unreachable or refusing the operation right now" and
# deserves a 503 with a retry hint. See https://www.postgresql.org/docs/current/errcodes-appendix.html
_OUTAGE_SQLSTATE_CLASSES = {"08", "53", "57"}  # connection / insufficient_resources / operator_intervention


def _is_db_outage(exc: SQLAlchemyError) -> bool:
    """True only for connection / infra failures, not for SQL or data bugs.

    A blanket ``except SQLAlchemyError -> 503`` (the old handler) turned every
    DB error into "Database unavailable" — so a typo in a query (ProgrammingError)
    or a violated constraint (IntegrityError) reported itself as an outage. That
    is how two unrelated bugs (AmbiguousParameter on document open, and the
    immutable-measurement trigger on signup) hid behind one misleading message.

    We classify by SQLAlchemy subclass first (OperationalError/InterfaceError are
    always connection-layer), then fall back to the driver's SQLSTATE class for
    cases SQLAlchemy doesn't subsume (e.g. a server-side admin shutdown, 57P01,
    still arrives as OperationalError but some clustered/proxy setups raise a
    bare DBAPIError with the state set).
    """
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return bool(sqlstate) and sqlstate[:2] in _OUTAGE_SQLSTATE_CLASSES


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    # Always log the real error with a full traceback — the handler must never
    # be the place where the root cause gets hidden. The status code + detail
    # message are what differ: outages 503 (clients retry / show "try again"),
    # everything else 500 with an honest "this request failed" message that does
    # NOT pretend the database is down.
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
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id", "X-Workspace-Mode"],
        expose_headers=["X-Zivo-Guest-Id"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id", "X-Workspace-Mode"],
        expose_headers=["X-Zivo-Guest-Id"],
    )

app.include_router(api_router, prefix="/api")


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}
