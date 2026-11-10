"""FastAPI application entry point. Owned by A1."""

from fastapi import FastAPI

from app.api.router import api_router

app = FastAPI(title="Echo.ai", version="0.1.0")
app.include_router(api_router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "Echo.ai", "status": "ready", "docs": "/docs"}
