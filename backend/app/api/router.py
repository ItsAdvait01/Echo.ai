"""Top-level route registration. A1 owns this shared integration point."""

from fastapi import APIRouter

from app.api.routes.executions import router as executions_router
from app.api.routes.health import router as health_router
from app.api.routes.learning import router as learning_router
from app.api.routes.voice import router as voice_router
from app.api.routes.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(learning_router)
api_router.include_router(workflows_router)
api_router.include_router(executions_router)
api_router.include_router(voice_router)
