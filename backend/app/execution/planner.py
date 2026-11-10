"""Bounded action planning. U3 owns this module.

Selects the next permitted workflow step after the last verified checkpoint.
Preserves step ordering and never repeats a verified step.
"""

from app.schemas.contracts import Workflow, WorkflowStep


class WorkflowPlanner:
    """Choose the next step to execute after the last verified checkpoint."""

    def next_step(self, workflow: Workflow, last_verified_order: int) -> WorkflowStep | None:
        """Return the next step to execute after the last verified checkpoint.

        Only approved workflows are planned. Steps are selected in ascending
        ``order``; the step immediately following ``last_verified_order`` is
        returned. Returns ``None`` when the workflow has no further step, which
        signals completion to the caller.
        """
        if not workflow or not workflow.approved:
            return None

        next_order = last_verified_order + 1
        for step in workflow.steps:
            if step.order == next_order:
                return step
        return None

    def plan(self, workflow: Workflow, last_verified_order: int) -> dict:
        """Produce a small structured plan around the next executable step."""
        step = self.next_step(workflow, last_verified_order)
        return {
            "workflow_id": workflow.id if workflow else None,
            "approved": bool(workflow and workflow.approved),
            "last_verified_order": last_verified_order,
            "next_step": step,
            "status": "planned" if step is not None else "completed",
        }
