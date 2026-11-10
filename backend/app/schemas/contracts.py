"""Initial shared contracts. A1 is the sole writer; changes require a request."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    LEARNING = "learning"
    PROCESSING = "processing"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    timestamp: datetime
    event_type: str
    page_url: str
    page_title: str = ""
    target_summary: str | None = None
    safe_value: str | None = None


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    order: int = Field(ge=1)
    action_type: str
    target: dict[str, Any] = Field(default_factory=dict)
    expected_result: dict[str, Any] = Field(default_factory=dict)
    risk: str = "low"


class WorkflowDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    trigger_phrases: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)


class Workflow(WorkflowDraft):
    id: str
    approved: bool = False
    created_at: datetime


class ExecutionRun(BaseModel):
    id: str
    workflow_id: str
    state: AgentState = AgentState.IDLE
    last_verified_step: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None


class StatusEvent(BaseModel):
    type: str
    state: AgentState
    message: str
    session_id: str | None = None
    run_id: str | None = None
    verification: dict[str, Any] | None = None
