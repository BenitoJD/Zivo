from fastapi import APIRouter

from app.api import chunked, health, internal

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(chunked.router)
api_router.include_router(internal.router)
