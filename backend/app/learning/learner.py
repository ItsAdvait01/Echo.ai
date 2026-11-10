"""Event interpretation and workflow-draft starter. U2 owns this module."""

from app.schemas.contracts import BrowserEvent, WorkflowDraft


class WorkflowLearner:
    def create_draft(self, events: list[BrowserEvent]) -> WorkflowDraft:
        """Return a reviewable draft; never approve or persist it here."""
        raise NotImplementedError("U2: interpret observed events into a validated draft")
