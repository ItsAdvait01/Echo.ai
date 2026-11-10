"""Top-level route registration. A1 owns this shared integration point."""

from fastapi import APIRouter

from app.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])

# A1 adds feature routers here after their contracts and ownership are agreed.
