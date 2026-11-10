"""Memory-only records for execution progress. Owned by A2.

`ExecutionRun` and `Workflow` come from the shared contracts owned by A1. A2
needs one extra record, the verified checkpoint, which is defined here until
A1 promotes it into `app/schemas/contracts.py` (see INTERFACE.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contracts import ExecutionRun


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp used for every memory record."""
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    """Serialize a timestamp for storage, preserving an explicit UTC offset."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def from_iso(value: str) -> datetime:
    """Parse a stored ISO timestamp; tolerant of earlier naive values."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class VerifiedCheckpoint(BaseModel):
    """A step that the verifier confirmed. Unverified attempts are never stored."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: str
    step_order: int = Field(ge=1)
    verified_at: datetime
    verification: dict[str, Any] = Field(default_factory=dict)


class ExecutionProgress(BaseModel):
    """Run state plus the verified checkpoints that back it."""

    model_config = ConfigDict(extra="forbid")

    run: ExecutionRun
    verified_checkpoints: list[VerifiedCheckpoint] = Field(default_factory=list)
