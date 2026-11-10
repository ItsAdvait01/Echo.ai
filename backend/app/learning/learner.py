"""Event interpretation and workflow-draft starter. U2 owns this module."""

from app.schemas.contracts import BrowserEvent, WorkflowDraft
from app.learning.service import WorkflowLearnerService


class WorkflowLearner:
    """Interpret observed browser events into a reviewable workflow draft.

    Never approves or persists a draft; that is a user decision handled at the
    A1/A2 approval boundary.
    """

    def __init__(self, service: WorkflowLearnerService | None = None) -> None:
        self._service = service or WorkflowLearnerService()

    def create_draft(self, events: list[BrowserEvent]) -> WorkflowDraft:
        """Return a reviewable draft; never approve or persist it here.

        The session id is taken from the validated event stream (all events in
        a stream share one session, enforced by :class:`EventProcessor`).
        """
        if not events:
            from app.learning.errors import WorkflowDraftError

            raise WorkflowDraftError(
                "empty_stream",
                "No events were provided for workflow learning.",
                {"count": 0},
            )
        session_id = events[0].session_id if isinstance(events[0], BrowserEvent) else None
        return self._service.generate_workflow_draft(session_id, events)

    @property
    def service(self) -> WorkflowLearnerService:
        return self._service
