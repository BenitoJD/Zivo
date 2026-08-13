from fastapi import APIRouter

from app.api import (
    activities,
    audiobook,
    debug,
    documents,
    guest,
    health,
    models,
    offline,
    reference,
    sources,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(guest.router, tags=["guest"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(activities.router, prefix="/activities", tags=["activities"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(debug.router, prefix="/debug", tags=["debug"])
api_router.include_router(offline.router, prefix="/offline", tags=["offline"])
api_router.include_router(audiobook.router, prefix="/audiobook", tags=["audiobook"])
api_router.include_router(reference.router, prefix="/reference", tags=["reference"])
