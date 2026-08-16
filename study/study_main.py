import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

import app.eta  # noqa: F401  register ETA handlers before artifact imports
from app.api import health
from study_api import (
    artifacts,
    assertions,
    chat,
    guest,
    learn,
    mcq,
    offline,
    progress,
    reference,
    study,
    topics,
)
from app.config import get_settings
from app.db import SessionLocal
from app.engine_runtime import choose, pick
from app.services.embed import set_active_embed_model
from app.services.llm_registry import bootstrap_llm_registry_from_env, resolve_embedding_model

logger = logging.getLogger(__name__)
settings = get_settings()


def _warmup_retrieval_models() -> None:
    from app.services.embed import embed_query

    embed_query("warmup")


@asynccontextmanager
async def lifespan(_: FastAPI):
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
    yield


app = FastAPI(
    title="Zivo Study",
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
    return isinstance(exc, (OperationalError, InterfaceError)) or (
        bool(sqlstate) and sqlstate[:2] in _OUTAGE_SQLSTATE_CLASSES
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


def _add_prod_middleware() -> None:
    app.add_middleware(HstsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id", "X-Workspace-Mode"],
        expose_headers=["X-Zivo-Guest-Id"],
    )


def _add_dev_middleware() -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Zivo-Guest-Id", "X-Workspace-Mode"],
        expose_headers=["X-Zivo-Guest-Id"],
    )


pick(settings.is_production, _add_prod_middleware, _add_dev_middleware)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(guest.router, tags=["guest"])
api_router.include_router(offline.router, prefix="/offline", tags=["offline"])
api_router.include_router(reference.router, prefix="/reference", tags=["reference"])
api_router.include_router(artifacts.router, prefix="/artifacts", tags=["artifacts"])
api_router.include_router(assertions.router, prefix="/assertions", tags=["assertions"])
api_router.include_router(learn.router, prefix="/artifacts", tags=["learn"])
api_router.include_router(topics.router, prefix="/artifacts", tags=["topics"])
api_router.include_router(study.router, prefix="/artifacts", tags=["study"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(mcq.router, prefix="/mcq", tags=["mcq"])
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])
app.include_router(api_router, prefix="/api")


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}
