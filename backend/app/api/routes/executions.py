"""
Execution routes — start, confirm, pause, stop, status.  Owned by A1.

Routes are thin; actual planning, execution, and verification are delegated
to U3 (planner + executor) and U4 (verifier + recovery).

Coordinator integration changes (logged):
* A step whose ``risk`` is HIGH is never executed on the first pass. The run
  moves to ``WAITING_FOR_CONFIRMATION`` and only ``POST /{run_id}/confirm``
  performs it, which is the explicit-confirmation rule from TRD §9.
* Verification outcomes are honoured as U4 reports them: only ``verified``
  advances ``last_verified_step``; ``unknown``/``needs_review``/``blocked``
  pause the run (a missing or ambiguous signal is never treated as success);
  a classified ``failed`` outcome consults the bounded retry policy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from app.core.dependencies import (
    get_browser_executor,
    get_checkpoint_verifier,
    get_recovery_policy,
    get_workflow_planner,
    get_workflow_repository,
)
from app.core.errors import InvalidStateError, NotFoundError
from app.execution.executor import BrowserExecutor
from app.execution.planner import WorkflowPlanner
from app.execution.recovery import RecoveryPolicy
from app.execution.verifier import CheckpointVerifier
from app.memory.repository import WorkflowRepository
from app.schemas.contracts import (
    AgentState,
    ExecutionResponse,
    ExecutionRun,
    ExecutionStartRequest,
    RiskLevel,
    VerificationResult,
    Workflow,
    WorkflowStep,
)

router = APIRouter(prefix="/executions", tags=["executions"])

# ---------------------------------------------------------------------------
# In-process run registry — thin lifecycle owned by A1;
# progress / checkpoints are delegated to U3/U4/A2.
# ---------------------------------------------------------------------------
_runs: dict[str, ExecutionRun] = {}
# Steps awaiting explicit confirmation before they may be performed.
_pending_steps: dict[str, WorkflowStep] = {}

_CONFIDENCE_BY_LEVEL = {
    "high": 0.95,
    "medium": 0.7,
    "low": 0.4,
    "insufficient": 0.1,
}


def _confidence_value(raw: Any) -> float:
    """Normalise U4's confidence level into the contract's 0..1 float."""
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    return _CONFIDENCE_BY_LEVEL.get(str(raw or "").lower(), 0.0)


def _attempt_step(
    run: ExecutionRun,
    step: WorkflowStep,
    executor: BrowserExecutor,
    verifier: CheckpointVerifier,
    recovery: RecoveryPolicy,
) -> tuple[VerificationResult, AgentState, str]:
    """Execute and verify one step. Never advances progress on its own."""
    observed = executor.execute(step)
    raw = verifier.verify(step, observed)

    status = str(raw.get("status") or "").lower()
    passed = status == "verified"
    blocked = str(observed.get("status") or "") == "blocked"

    verification = VerificationResult(
        step_id=step.id,
        step_order=step.order,
        passed=passed,
        confidence=_confidence_value(raw.get("confidence")),
        observed=observed,
        message=str(raw.get("reason") or raw.get("message") or ""),
    )

    if passed:
        return verification, AgentState.EXECUTING, f"Step {step.order} executed and verified."

    if blocked:
        detail = (observed.get("error") or {}).get("message", "target cannot be resolved")
        return (
            verification,
            AgentState.PAUSED,
            f"Step {step.order} could not be performed ({detail}); paused for review.",
        )

    if status in ("unknown", "needs_review", "blocked"):
        # No trustworthy success signal: never claim progress.
        return (
            verification,
            AgentState.PAUSED,
            f"Step {step.order} has no verifiable success signal; paused instead of guessing.",
        )

    if recovery.should_retry(
        attempt=1, confidence=verification.confidence, ambiguous_target=False
    ):
        return (
            verification,
            AgentState.PAUSED,
            f"Step {step.order} verification failed; paused for retry.",
        )
    return (
        verification,
        AgentState.FAILED,
        f"Step {step.order} verification failed; execution stopped.",
    )


def _finish_if_complete(
    run: ExecutionRun, workflow: Workflow, planner: WorkflowPlanner
) -> None:
    """Mark the run complete only when no unverified step remains."""
    if run.state != AgentState.EXECUTING:
        return
    if planner.next_step(workflow, run.last_verified_step) is None:
        run.state = AgentState.COMPLETED
        run.ended_at = datetime.now(UTC)


def _advance(
    run: ExecutionRun,
    workflow: Workflow,
    planner: WorkflowPlanner,
    executor: BrowserExecutor,
    verifier: CheckpointVerifier,
    recovery: RecoveryPolicy,
) -> tuple[WorkflowStep | None, VerificationResult | None, str]:
    """Carry the run forward until it completes, fails, or needs confirmation.

    A run that only performed the current step would stall in ``executing``
    until someone pressed resume again, so every verified step is followed by
    the next one. A HIGH-risk step is never performed here: it is staged as the
    pending step and the run stops in ``waiting_for_confirmation``.
    """
    _pending_steps.pop(run.id, None)
    current_step: WorkflowStep | None = None
    verification: VerificationResult | None = None
    attempted: set[int] = set()

    while True:
        step = planner.next_step(workflow, run.last_verified_step)
        if step is None:
            run.state = AgentState.COMPLETED
            run.ended_at = datetime.now(UTC)
            return current_step, verification, "Workflow complete; every step verified."
        if step.order in attempted:
            # The planner returned a step this loop already handled.
            run.state = AgentState.FAILED
            run.ended_at = datetime.now(UTC)
            return current_step, verification, (
                f"Execution stopped: step {step.order} repeated without advancing."
            )

        current_step = step
        if step.risk == RiskLevel.HIGH:
            _pending_steps[run.id] = step
            run.state = AgentState.WAITING_FOR_CONFIRMATION
            return current_step, verification, (
                f"Step {step.order} is marked high risk; "
                "explicit confirmation is required before it runs."
            )

        attempted.add(step.order)
        verification, state, message = _attempt_step(
            run, step, executor, verifier, recovery
        )
        run.state = state
        if state != AgentState.EXECUTING:
            return current_step, verification, message
        run.last_verified_step = step.order


@router.post("", response_model=ExecutionResponse)
def start_execution(
    body: ExecutionStartRequest,
    repo: WorkflowRepository = Depends(get_workflow_repository),  # type: ignore[type-arg]
    planner: WorkflowPlanner = Depends(get_workflow_planner),
    executor: BrowserExecutor = Depends(get_browser_executor),
    verifier: CheckpointVerifier = Depends(get_checkpoint_verifier),
    recovery: RecoveryPolicy = Depends(get_recovery_policy),
) -> ExecutionResponse:
    """Start (or resume) execution of an approved workflow."""
    workflow = repo.get(body.workflow_id)
    if workflow is None:
        raise NotFoundError("Workflow", body.workflow_id)
    if not workflow.approved:
        raise InvalidStateError("Cannot execute an unapproved workflow.")

    run_id = uuid.uuid4().hex[:12]
    resume_step = body.resume_from_step or 0

    run = ExecutionRun(
        id=run_id,
        workflow_id=body.workflow_id,
        state=AgentState.EXECUTING,
        last_verified_step=resume_step,
        started_at=datetime.now(UTC),
    )
    _runs[run_id] = run

    message = "Execution started."
    current_step: WorkflowStep | None = None
    verification: VerificationResult | None = None

    try:
        current_step, verification, message = _advance(
            run, workflow, planner, executor, verifier, recovery
        )
    except NotImplementedError:
        # Agent modules are stubs — run is registered but not actively executing
        run.state = AgentState.PAUSED
        message = "Execution registered. Agent modules not yet implemented (stub)."
    except ValueError as exc:
        run.state = AgentState.FAILED
        run.ended_at = datetime.now(UTC)
        message = f"Execution failed: {exc}"

    _runs[run_id] = run
    return ExecutionResponse(
        run=run,
        message=message,
        current_step=current_step,
        verification=verification,
    )


@router.post("/{run_id}/confirm", response_model=ExecutionResponse)
def confirm_execution_step(
    run_id: str,
    repo: WorkflowRepository = Depends(get_workflow_repository),  # type: ignore[type-arg]
    planner: WorkflowPlanner = Depends(get_workflow_planner),
    executor: BrowserExecutor = Depends(get_browser_executor),
    verifier: CheckpointVerifier = Depends(get_checkpoint_verifier),
    recovery: RecoveryPolicy = Depends(get_recovery_policy),
) -> ExecutionResponse:
    """Perform the step that is waiting for explicit user confirmation."""
    run = _runs.get(run_id)
    if run is None:
        raise NotFoundError("Execution run", run_id)
    if run.state != AgentState.WAITING_FOR_CONFIRMATION:
        raise InvalidStateError(
            f"Run is in state '{run.state.value}'; nothing is awaiting confirmation."
        )

    step = _pending_steps.pop(run_id, None)
    if step is None:
        raise InvalidStateError("No pending step is awaiting confirmation.")

    workflow = repo.get(run.workflow_id)
    if workflow is None:
        raise NotFoundError("Workflow", run.workflow_id)

    run.state = AgentState.EXECUTING
    current_step: WorkflowStep | None = step
    verification, state, message = _attempt_step(run, step, executor, verifier, recovery)
    run.state = state
    if state == AgentState.EXECUTING:
        run.last_verified_step = step.order
        # Keep going: the next step may run, or wait for its own confirmation.
        follow_step, follow_verification, follow_message = _advance(
            run, workflow, planner, executor, verifier, recovery
        )
        if follow_step is not None:
            current_step = follow_step
        if follow_verification is not None:
            verification = follow_verification
        if follow_step is not None or run.state == AgentState.COMPLETED:
            message = follow_message

    _runs[run_id] = run
    return ExecutionResponse(
        run=run,
        message=message,
        current_step=current_step,
        verification=verification,
    )


@router.get("/{run_id}", response_model=ExecutionResponse)
def get_execution_status(run_id: str) -> ExecutionResponse:
    """Read current run status and verified progress."""
    run = _runs.get(run_id)
    if run is None:
        raise NotFoundError("Execution run", run_id)
    pending = _pending_steps.get(run_id)
    if pending is not None:
        message = (
            f"Run is {run.state.value}: step {pending.order} awaits confirmation."
        )
    else:
        message = f"Run is {run.state.value}."
    return ExecutionResponse(run=run, message=message, current_step=pending)


@router.post("/{run_id}/pause", response_model=ExecutionResponse)
def pause_execution(run_id: str) -> ExecutionResponse:
    """Pause a running execution safely."""
    run = _runs.get(run_id)
    if run is None:
        raise NotFoundError("Execution run", run_id)

    if run.state not in (
        AgentState.EXECUTING,
        AgentState.WAITING_FOR_CONFIRMATION,
        AgentState.PAUSED,  # allow re-confirming pause (idempotent)
    ):
        raise InvalidStateError(f"Cannot pause a run in state '{run.state.value}'.")

    run.state = AgentState.PAUSED
    _runs[run_id] = run
    return ExecutionResponse(run=run, message="Execution paused.")


@router.post("/{run_id}/stop", response_model=ExecutionResponse)
def stop_execution(run_id: str) -> ExecutionResponse:
    """Stop a running or paused execution."""
    run = _runs.get(run_id)
    if run is None:
        raise NotFoundError("Execution run", run_id)

    terminal = (AgentState.COMPLETED, AgentState.FAILED)
    if run.state in terminal:
        raise InvalidStateError(f"Run is already in terminal state '{run.state.value}'.")

    _pending_steps.pop(run_id, None)
    run.state = AgentState.COMPLETED
    run.ended_at = datetime.now(UTC)
    _runs[run_id] = run
    return ExecutionResponse(run=run, message="Execution stopped by user.")
