"""Errors raised by the workflow memory layer. Owned by A2."""

from __future__ import annotations


class MemoryError(Exception):
    """Base class for workflow memory failures."""


class MemoryValidationError(MemoryError):
    """The caller supplied data that cannot be stored safely."""


class WorkflowNotFoundError(MemoryError):
    """No workflow exists with the requested id."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"Workflow {workflow_id!r} was not found.")
        self.workflow_id = workflow_id


class RunNotFoundError(MemoryError):
    """No execution run exists with the requested id."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"Execution run {run_id!r} was not found.")
        self.run_id = run_id


class UnapprovedWorkflowError(MemoryError):
    """A workflow may not be saved, returned, or executed before approval."""

    def __init__(self, workflow_id: str, detail: str = "") -> None:
        message = f"Workflow {workflow_id!r} is not approved for reuse."
        if detail:
            message = f"{message} {detail}"
        super().__init__(message)
        self.workflow_id = workflow_id


class UnverifiedProgressError(MemoryError):
    """Progress cannot advance without a successful verification result."""
