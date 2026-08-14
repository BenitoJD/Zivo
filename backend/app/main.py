import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# Hide /docs, /redoc, /openapi.json in production. The ingress routes "/" with
# Prefix, so without this the health surface would be publicly discoverable at
# api.zivo.fyi. Dev keeps the docs for local iteration. ETA scheduler runs on
# the IO worker process, not this health-only API.
app = FastAPI(
    title="Zivo API",
    version="0.1.0",
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
