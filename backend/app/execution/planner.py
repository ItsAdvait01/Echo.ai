"""Bounded action-planning starter. U3 owns this module."""

from app.schemas.contracts import Workflow, WorkflowStep


class WorkflowPlanner:
    def next_step(self, workflow: Workflow, last_verified_order: int) -> WorkflowStep | None:
        """Choose the next step after the last verified checkpoint."""
        raise NotImplementedError("U3: inspect state and select a permitted next step")
