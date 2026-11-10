"""
Shared Pydantic contracts — the single source of truth for all request/response
payloads and domain models across agents.

OWNED EXCLUSIVELY BY A1.  All changes require A1 + Advait approval and a
broadcast to every affected agent (A2, A3, A4, U1–U4) before implementation.

Version: 1.0.0-frozen
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AgentState(StrEnum):
    """Visible agent status; UI must always display the current label."""
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


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"  # requires explicit user confirmation


class VoiceIntent(StrEnum):
    """Normalized voice/text command intents per TRD §6."""
    TEACH_START = "teach.start"
    TEACH_STOP = "teach.stop"
    WORKFLOW_EXECUTE = "workflow.execute"
    EXECUTION_PAUSE = "execution.pause"
    EXECUTION_STOP = "execution.stop"
    STATUS_GET = "status.get"


# ---------------------------------------------------------------------------
# Domain models  (consumed by multiple agents)
# ---------------------------------------------------------------------------

class BrowserEvent(BaseModel):
    """A single normalized, privacy-filtered browser event emitted by U1."""
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    timestamp: datetime
    event_type: str          # e.g. "click", "navigate", "input", "scroll"
    page_url: str
    page_title: str = ""
    target_summary: str | None = None   # semantic locator, not coordinates
    safe_value: str | None = None       # never passwords/payment data


class WorkflowStep(BaseModel):
    """One ordered action inside a workflow."""
    model_config = ConfigDict(extra="forbid")

    id: str
    order: int = Field(ge=1)
    action_type: str             # must be in BrowserExecutor.ALLOWED_ACTIONS
    target: dict[str, Any] = Field(default_factory=dict)
    expected_result: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW


class WorkflowDraft(BaseModel):
    """Proposed workflow awaiting user review — not yet runnable."""
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    trigger_phrases: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)


class Workflow(WorkflowDraft):
    """Persisted workflow — becomes runnable only when approved=True."""
    id: str
    approved: bool = False
    created_at: datetime


class ExecutionRun(BaseModel):
    """A single execution attempt of a workflow."""
    id: str
    workflow_id: str
    state: AgentState = AgentState.IDLE
    last_verified_step: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None


class VerificationResult(BaseModel):
    """Outcome of a single step verification by U4."""
    step_id: str
    step_order: int
    passed: bool
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    observed: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class StatusEvent(BaseModel):
    """State change event pushed to the frontend / voice layer."""
    type: str                   # e.g. "state_change", "verification", "error"
    state: AgentState
    message: str
    session_id: str | None = None
    run_id: str | None = None
    verification: VerificationResult | None = None


# ---------------------------------------------------------------------------
# API request schemas
# ---------------------------------------------------------------------------

class TeachStartRequest(BaseModel):
    """POST /api/learning/start — begin a new observation session."""
    model_config = ConfigDict(extra="forbid")

    target_url: str = Field(
        ..., description="The URL of the site/page to observe."
    )
    consent_confirmed: bool = Field(
        ..., description="User must explicitly confirm observation consent."
    )


class TeachStopRequest(BaseModel):
    """POST /api/learning/{session_id}/stop — stop and request draft."""
    model_config = ConfigDict(extra="forbid")

    generate_draft: bool = Field(
        default=True,
        description="Whether to immediately generate a workflow draft.",
    )


class WorkflowApproveRequest(BaseModel):
    """POST /api/workflows/{workflow_id}/approve — approve a draft."""
    model_config = ConfigDict(extra="forbid")

    approved: bool = Field(
        ..., description="True = approve and persist; False = reject."
    )
    name_override: str | None = Field(
        default=None, description="Optional user rename before saving."
    )


class ExecutionStartRequest(BaseModel):
    """POST /api/executions — start or resume a workflow execution."""
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    resume_from_step: int | None = Field(
        default=None,
        description="If set, resume from this verified step instead of step 1.",
    )


class VoiceCommandRequest(BaseModel):
    """POST /api/voice/command — text-or-transcript command input."""
    model_config = ConfigDict(extra="forbid")

    transcript: str = Field(
        ..., description="User utterance (voice transcript or typed text).",
    )
    source: str = Field(
        default="text",
        description="'voice' for real STT, 'text' for fallback.",
    )


# ---------------------------------------------------------------------------
# API response schemas
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    """Standardized error payload per TRD §10."""
    code: str                # stable machine-readable code
    message: str             # user-readable description
    retryable: bool = False


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "echo-api"
    version: str = "0.1.0"


class TeachSessionResponse(BaseModel):
    """Response for teach start / stop / event retrieval."""
    session_id: str
    state: AgentState
    message: str
    event_count: int = 0
    events: list[BrowserEvent] = Field(default_factory=list)
    draft: WorkflowDraft | None = None


class WorkflowResponse(BaseModel):
    """Single workflow detail."""
    workflow: Workflow
    message: str = ""


class WorkflowListResponse(BaseModel):
    """List of approved workflows."""
    workflows: list[Workflow]
    count: int


class ExecutionResponse(BaseModel):
    """Execution run status and progress."""
    run: ExecutionRun
    message: str = ""
    current_step: WorkflowStep | None = None
    verification: VerificationResult | None = None


class VoiceCommandResponse(BaseModel):
    """Normalized intent and routed action result."""
    intent: VoiceIntent | None = None
    transcript: str
    action_result: dict[str, Any] = Field(default_factory=dict)
    reply_text: str = ""
    source: str = "text"
