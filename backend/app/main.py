"""
FastAPI application entry point.  Owned by A1.

Registers routers, CORS middleware, and consistent error handlers.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import (
    EchoError,
    echo_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# -- CORS (frontend on Vite dev server) ------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Error handlers ---------------------------------------------------------
app.add_exception_handler(EchoError, echo_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]

# -- Routers ----------------------------------------------------------------
app.include_router(api_router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.APP_NAME, "status": "ready", "docs": "/docs"}
