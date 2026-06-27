from fastapi import APIRouter

from app.api import (
    activities,
    artifacts,
    assertions,
    auth,
    chat,
    chunked_uploads,
    documents,
    health,
    learn,
    mcq,
    models,
    practice,
    sources,
    topics,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(
    chunked_uploads.router, prefix="/sources/chunked", tags=["chunked-uploads"]
)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(artifacts.router, prefix="/artifacts", tags=["artifacts"])
api_router.include_router(activities.router, prefix="/activities", tags=["activities"])
api_router.include_router(assertions.router, prefix="/assertions", tags=["assertions"])
api_router.include_router(learn.router, prefix="/artifacts", tags=["learn"])
api_router.include_router(topics.router, prefix="/artifacts", tags=["topics"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(mcq.router, prefix="/mcq", tags=["mcq"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(practice.router, prefix="/practice", tags=["practice"])
