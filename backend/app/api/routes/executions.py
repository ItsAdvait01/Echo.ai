"""
Execution routes — start, pause, stop, status.  Owned by A1.

Routes are thin; actual planning, execution, and verification are delegated
to U3 (planner + executor) and U4 (verifier + recovery).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
    VerificationResult,
)

router = APIRouter(prefix="/executions", tags=["executions"])

# ---------------------------------------------------------------------------
# In-process run registry — thin lifecycle owned by A1;
# progress / checkpoints are delegated to U3/U4/A2.
# ---------------------------------------------------------------------------
_runs: dict[str, ExecutionRun] = {}


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
        started_at=datetime.now(timezone.utc),
    )
    _runs[run_id] = run

    # Attempt to execute the next step via U3 → U4
    message = "Execution started."
    current_step = None
    verification: VerificationResult | None = None

    try:
        step = planner.next_step(workflow, resume_step)
        if step is None:
            run.state = AgentState.COMPLETED
            run.ended_at = datetime.now(timezone.utc)
            message = "All steps already verified — workflow complete."
        else:
            current_step = step
            # Execute through U3
            observed = executor.execute(step)
            # Verify through U4
            result = verifier.verify(step, observed)
            verification = VerificationResult(
                step_id=step.id,
                step_order=step.order,
                passed=result.get("passed", False),
                confidence=result.get("confidence", 0.0),
                observed=result.get("observed", {}),
                message=result.get("message", ""),
            )
            if verification.passed:
                run.last_verified_step = step.order
                message = f"Step {step.order} executed and verified."
            else:
                # Verification failed — do not advance checkpoint
                if recovery.should_retry(attempt=1, confidence=verification.confidence,
                                         ambiguous_target=False):
                    run.state = AgentState.PAUSED
                    message = f"Step {step.order} verification failed; paused for retry."
                else:
                    run.state = AgentState.FAILED
                    run.ended_at = datetime.now(timezone.utc)
                    message = f"Step {step.order} verification failed; execution stopped."
    except NotImplementedError:
        # Agent modules are stubs — run is registered but not actively executing
        run.state = AgentState.PAUSED
        message = "Execution registered. Agent modules not yet implemented (stub)."
    except ValueError as exc:
        run.state = AgentState.FAILED
        run.ended_at = datetime.now(timezone.utc)
        message = f"Execution failed: {exc}"

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
    return ExecutionResponse(run=run, message=f"Run is {run.state.value}.")


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
        raise InvalidStateError(
            f"Cannot pause a run in state '{run.state.value}'."
        )

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
        raise InvalidStateError(
            f"Run is already in terminal state '{run.state.value}'."
        )

    run.state = AgentState.COMPLETED
    run.ended_at = datetime.now(timezone.utc)
    _runs[run_id] = run
    return ExecutionResponse(run=run, message="Execution stopped by user.")
