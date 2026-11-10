"""Repository factory used by the API layer. Owned by A2.

Default is durable SQLite. ``ECHO_MEMORY_BACKEND=memory`` selects the temporary
in-memory adapter for environments without a writable disk. There is no silent
fallback: a broken SQLite setup raises instead of pretending data was saved.
"""

from __future__ import annotations

import logging
import os
import threading

from app.memory.repository import InMemoryWorkflowRepository, WorkflowRepository
from app.memory.sqlite_repository import SQLiteWorkflowRepository

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "echo.db"
MEMORY_BACKENDS = frozenset({"memory", "in-memory", "in_memory"})

_lock = threading.Lock()
_repository: WorkflowRepository | None = None


def create_repository(
    *, backend: str | None = None, db_path: str | None = None
) -> WorkflowRepository:
    """Build a repository from explicit arguments or environment variables."""
    choice = (backend or os.getenv("ECHO_MEMORY_BACKEND") or "sqlite").strip().lower()
    if choice in MEMORY_BACKENDS:
        logger.warning(
            "Echo.ai workflow memory is using the TEMPORARY in-memory adapter; "
            "workflows will not survive a restart."
        )
        return InMemoryWorkflowRepository()
    if choice != "sqlite":
        raise ValueError(
            f"Unsupported ECHO_MEMORY_BACKEND={choice!r}; use 'sqlite' or 'memory'."
        )
    path = db_path or os.getenv("ECHO_MEMORY_DB") or DEFAULT_DB_PATH
    return SQLiteWorkflowRepository(path)


def get_repository() -> WorkflowRepository:
    """Process-wide repository singleton for API and execution modules."""
    global _repository
    if _repository is None:
        with _lock:
            if _repository is None:
                _repository = create_repository()
    return _repository


def reset_repository(repository: WorkflowRepository | None = None) -> None:
    """Replace/clear the singleton; intended for tests and local scripts."""
    global _repository
    with _lock:
        _repository = repository


__all__ = [
    "create_repository",
    "get_repository",
    "reset_repository",
]
