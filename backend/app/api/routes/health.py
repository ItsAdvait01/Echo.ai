"""Health endpoint. Owned by A1."""

from fastapi import APIRouter

from app.schemas.contracts import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
