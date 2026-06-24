import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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


app = FastAPI(title="Zivo API", version="0.1.0", lifespan=lifespan)


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
