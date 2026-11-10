"""Persistence starter. A2 owns this module and implements the repository."""

from typing import Protocol

from app.schemas.contracts import Workflow


class WorkflowRepository(Protocol):
    def get(self, workflow_id: str) -> Workflow | None: ...

    def list_approved(self) -> list[Workflow]: ...

    def save_approved(self, workflow: Workflow) -> Workflow: ...


class InMemoryWorkflowRepository:
    """Temporary adapter for early wiring; replace/extend with SQLite in A2."""

    def __init__(self) -> None:
        self._items: dict[str, Workflow] = {}

    def get(self, workflow_id: str) -> Workflow | None:
        return self._items.get(workflow_id)

    def list_approved(self) -> list[Workflow]:
        return [item for item in self._items.values() if item.approved]

    def save_approved(self, workflow: Workflow) -> Workflow:
        if not workflow.approved:
            raise ValueError("Only approved workflows may be saved as reusable memory.")
        self._items[workflow.id] = workflow
        return workflow
