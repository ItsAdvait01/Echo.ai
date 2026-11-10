"""Application configuration. Owned by A1."""

from __future__ import annotations

import os
from typing import ClassVar


class Settings:
    """Simple settings read from environment — no secrets committed."""

    APP_NAME: str = "Echo.ai"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./echo.db")
    CORS_ORIGINS: ClassVar[list[str]] = [
        "http://localhost:5173",  # Vite default
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ]


settings = Settings()
