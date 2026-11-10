"""Workflow memory interface and in-memory adapter. Owned by A2.

API, planner/executor, and verifier modules consume the repository through
``app.memory.factory.get_repository()`` so storage can swap between SQLite and
the temporary in-memory adapter without touching call sites.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Protocol

from app.memory.errors import (
    MemoryValidationError,
    RunNotFoundError,
    UnapprovedWorkflowError,
    UnverifiedProgressError,
    WorkflowNotFoundError,
)
from app.memory.models import ExecutionProgress, VerifiedCheckpoint, utcnow
from app.memory.validation import require_verification, validate_runnable
from app.schemas.contracts import AgentState, ExecutionRun, Workflow

TERMINAL_RUN_STATES = frozenset({AgentState.COMPLETED, AgentState.FAILED})


class WorkflowRepository(Protocol):
    """Storage boundary for approved workflows and verified execution progress."""

    @property
    def is_durable(self) -> bool:
        """False only for the explicitly temporary in-memory adapter."""
        ...

    # --- workflow draft and approval lifecycle -------------------------
    def save_draft(self, workflow: Workflow) -> Workflow:
        """Persist an unapproved draft for review. Drafts are not runnable."""
        ...

    def approve(self, workflow_id: str) -> Workflow:
        """Mark an existing draft approved so it becomes reusable memory."""
        ...

    def reject(self, workflow_id: str) -> Workflow:
        """Keep a draft as non-runnable memory after review was rejected."""
        ...

    def save_approved(self, workflow: Workflow) -> Workflow:
        """Insert or update a workflow that the caller already approved."""
        ...

    # --- retrieval ------------------------------------------------------
    def get(self, workflow_id: str) -> Workflow | None:
        """Return an approved workflow, or None for missing/unapproved ids."""
        ...

    def get_draft(self, workflow_id: str) -> Workflow | None:
        """Return a workflow in any approval state (used by the review route)."""
        ...

    def list_approved(self) -> list[Workflow]:
        """Return only workflows usable as executable memory."""
        ...

    def list_all(self) -> list[Workflow]:
        """Return every stored workflow, for review/library screens."""
        ...

    # --- execution runs and verified checkpoints ------------------------
    def create_run(self, workflow_id: str, *, run_id: str | None = None) -> ExecutionRun:
        """Start a run; unapproved workflows cannot be executed."""
        ...

    def get_run(self, run_id: str) -> ExecutionRun | None: ...

    def list_runs(self, workflow_id: str | None = None) -> list[ExecutionRun]: ...

    def set_run_state(
        self, run_id: str, state: AgentState, *, ended_at: Any | None = None
    ) -> ExecutionRun:
        """Record pause/stop/completion. Terminal states stamp ended_at."""
        ...

    def record_verified_progress(
        self,
        run_id: str,
        *,
        step_order: int,
        verified: bool,
        verification: dict[str, Any] | None = None,
    ) -> ExecutionProgress:
        """Advance ``last_verified_step`` only for a verified checkpoint."""
        ...

    def get_progress(self, run_id: str) -> ExecutionProgress: ...

    def get_latest_progress(self, workflow_id: str) -> ExecutionProgress | None:
        """Latest run progress for a workflow (workflow-library summary)."""
        ...


class InMemoryWorkflowRepository:
    """Temporary adapter: same interface, process-lifetime storage only.

    Selected with ``ECHO_MEMORY_BACKEND=memory`` when SQLite is unavailable.
    Nothing survives a restart, so the API must not call it durable.
    """

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._runs: dict[str, ExecutionRun] = {}
        self._checkpoints: dict[str, list[VerifiedCheckpoint]] = {}
        self._lock = threading.RLock()

    @property
    def is_durable(self) -> bool:
        return False

    # --- workflow draft and approval lifecycle -------------------------
    def save_draft(self, workflow: Workflow) -> Workflow:
        if workflow.approved:
            raise UnapprovedWorkflowError(
                workflow.id, "save_draft only accepts unapproved drafts; use save_approved()."
            )
        stored = workflow.model_copy(deep=True)
        with self._lock:
            self._workflows[workflow.id] = stored
        return stored.model_copy(deep=True)

    def approve(self, workflow_id: str) -> Workflow:
        with self._lock:
            stored = self._workflows.get(workflow_id)
            if stored is None:
                raise WorkflowNotFoundError(workflow_id)
            if not stored.approved:
                stored = stored.model_copy(update={"approved": True})
                validate_runnable(stored)
                self._workflows[workflow_id] = stored
            return stored.model_copy(deep=True)

    def reject(self, workflow_id: str) -> Workflow:
        with self._lock:
            stored = self._workflows.get(workflow_id)
            if stored is None:
                raise WorkflowNotFoundError(workflow_id)
            stored = stored.model_copy(update={"approved": False})
            self._workflows[workflow_id] = stored
            return stored.model_copy(deep=True)

    def save_approved(self, workflow: Workflow) -> Workflow:
        validate_runnable(workflow)
        stored = workflow.model_copy(deep=True)
        with self._lock:
            self._workflows[workflow.id] = stored
        return stored.model_copy(deep=True)

    # --- retrieval ------------------------------------------------------
    def get(self, workflow_id: str) -> Workflow | None:
        with self._lock:
            stored = self._workflows.get(workflow_id)
            if stored is None or not stored.approved:
                return None
            return stored.model_copy(deep=True)

    def get_draft(self, workflow_id: str) -> Workflow | None:
        with self._lock:
            stored = self._workflows.get(workflow_id)
            return None if stored is None else stored.model_copy(deep=True)

    def list_approved(self) -> list[Workflow]:
        with self._lock:
            items = [w for w in self._workflows.values() if w.approved]
        return [w.model_copy(deep=True) for w in sorted(items, key=lambda w: w.created_at)]

    def list_all(self) -> list[Workflow]:
        with self._lock:
            items = list(self._workflows.values())
        return [w.model_copy(deep=True) for w in sorted(items, key=lambda w: w.created_at)]

    # --- execution runs and verified checkpoints ------------------------
    def create_run(self, workflow_id: str, *, run_id: str | None = None) -> ExecutionRun:
        with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(workflow_id)
            if not workflow.approved:
                raise UnapprovedWorkflowError(
                    workflow_id, "Only approved workflows can be executed."
                )
            new_id = run_id or uuid.uuid4().hex
            if new_id in self._runs:
                raise MemoryValidationError(f"Execution run {new_id!r} already exists.")
            run = ExecutionRun(
                id=new_id,
                workflow_id=workflow_id,
                state=AgentState.EXECUTING,
                last_verified_step=0,
                started_at=utcnow(),
            )
            self._runs[new_id] = run
            self._checkpoints[new_id] = []
            return run.model_copy(deep=True)

    def get_run(self, run_id: str) -> ExecutionRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else run.model_copy(deep=True)

    def list_runs(self, workflow_id: str | None = None) -> list[ExecutionRun]:
        with self._lock:
            runs = [r for r in self._runs.values() if workflow_id in (None, r.workflow_id)]
        runs.sort(key=lambda r: r.started_at.timestamp() if r.started_at else 0.0, reverse=True)
        return [run.model_copy(deep=True) for run in runs]

    def set_run_state(
        self, run_id: str, state: AgentState, *, ended_at: Any | None = None
    ) -> ExecutionRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            updates: dict[str, Any] = {"state": state}
            if ended_at is not None:
                updates["ended_at"] = ended_at
            elif state in TERMINAL_RUN_STATES and run.ended_at is None:
                updates["ended_at"] = utcnow()
            updated = run.model_copy(update=updates)
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def record_verified_progress(
        self,
        run_id: str,
        *,
        step_order: int,
        verified: bool,
        verification: dict[str, Any] | None = None,
    ) -> ExecutionProgress:
        evidence = require_verification(
            step_order=step_order, verified=verified, verification=verification
        )
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            workflow = self._workflows.get(run.workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(run.workflow_id)
            if all(step.order != step_order for step in workflow.steps):
                raise MemoryValidationError(
                    f"Workflow {workflow.id!r} has no step with order {step_order}."
                )

            checkpoints = self._checkpoints.setdefault(run_id, [])
            if all(checkpoint.step_order != step_order for checkpoint in checkpoints):
                checkpoints.append(
                    VerifiedCheckpoint(
                        run_id=run_id,
                        workflow_id=workflow.id,
                        step_order=step_order,
                        verified_at=utcnow(),
                        verification=evidence,
                    )
                )
                checkpoints.sort(key=lambda checkpoint: checkpoint.step_order)

            if step_order > run.last_verified_step:
                self._runs[run_id] = run.model_copy(
                    update={"last_verified_step": step_order}
                )
            return self._progress(run_id)

    def get_progress(self, run_id: str) -> ExecutionProgress:
        with self._lock:
            return self._progress(run_id)

    def get_latest_progress(self, workflow_id: str) -> ExecutionProgress | None:
        runs = self.list_runs(workflow_id)
        return self.get_progress(runs[0].id) if runs else None

    def _progress(self, run_id: str) -> ExecutionProgress:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        checkpoints = sorted(
            self._checkpoints.get(run_id, []), key=lambda checkpoint: checkpoint.step_order
        )
        return ExecutionProgress(
            run=run.model_copy(deep=True),
            verified_checkpoints=[c.model_copy(deep=True) for c in checkpoints],
        )


__all__ = [
    "TERMINAL_RUN_STATES",
    "InMemoryWorkflowRepository",
    "UnapprovedWorkflowError",
    "UnverifiedProgressError",
    "WorkflowRepository",
]
