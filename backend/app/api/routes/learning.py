"""
Teach-mode (learning) routes.  Owned by A1.

Thin routing layer — delegates to U1 (observer) and U2 (learner) modules.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.browser.observer import BrowserObserver
from app.core.dependencies import (
    get_browser_observer,
    get_workflow_learner,
)
from app.core.errors import ConsentRequiredError, InvalidStateError, NotFoundError
from app.learning.learner import WorkflowLearner
from app.schemas.contracts import (
    AgentState,
    BrowserEvent,
    TeachSessionResponse,
    TeachStartRequest,
    TeachStopRequest,
    WorkflowDraft,
)

router = APIRouter(prefix="/learning", tags=["learning"])

# ---------------------------------------------------------------------------
# In-process session registry — thin lifecycle owned by A1;
# actual observation logic is delegated to U1's BrowserObserver.
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}


@router.post("/start", response_model=TeachSessionResponse)
def start_teach_session(
    body: TeachStartRequest,
    observer: BrowserObserver = Depends(get_browser_observer),
) -> TeachSessionResponse:
    """Start a new observation session after explicit user consent."""
    if not body.consent_confirmed:
        raise ConsentRequiredError()

    session_id = uuid.uuid4().hex[:12]

    # Delegate observation to U1
    try:
        observer.start(session_id)
    except NotImplementedError:
        # U1 stub — session is registered but observation is not yet live.
        pass

    _sessions[session_id] = {
        "state": AgentState.LEARNING,
        "target_url": body.target_url,
        "started_at": datetime.now(timezone.utc),
        "stopped": False,
    }

    return TeachSessionResponse(
        session_id=session_id,
        state=AgentState.LEARNING,
        message=f"Observation session started for {body.target_url}.",
    )


@router.post("/{session_id}/stop", response_model=TeachSessionResponse)
def stop_teach_session(
    session_id: str,
    body: TeachStopRequest | None = None,
    observer: BrowserObserver = Depends(get_browser_observer),
    learner: WorkflowLearner = Depends(get_workflow_learner),
) -> TeachSessionResponse:
    """Stop observation and optionally generate a workflow draft."""
    session = _sessions.get(session_id)
    if session is None:
        raise NotFoundError("Session", session_id)
    if session["stopped"]:
        raise InvalidStateError(f"Session '{session_id}' is already stopped.")

    # Delegate stop to U1
    try:
        observer.stop(session_id)
    except NotImplementedError:
        pass

    session["stopped"] = True
    session["state"] = AgentState.PROCESSING

    # Retrieve captured events from U1
    events: list[BrowserEvent] = []
    try:
        events = observer.events(session_id)
    except NotImplementedError:
        pass

    # Optionally generate draft via U2
    generate = True if body is None else body.generate_draft
    draft: WorkflowDraft | None = None
    if generate and events:
        try:
            draft = learner.create_draft(events)
            session["state"] = AgentState.AWAITING_APPROVAL
        except NotImplementedError:
            session["state"] = AgentState.PROCESSING

    state = AgentState(session["state"])
    return TeachSessionResponse(
        session_id=session_id,
        state=state,
        message="Observation stopped." + (
            " Draft generated for review." if draft else " No draft generated (awaiting agent modules)."
        ),
        event_count=len(events),
        events=events,
        draft=draft,
    )


@router.get("/{session_id}/events", response_model=TeachSessionResponse)
def get_session_events(
    session_id: str,
    observer: BrowserObserver = Depends(get_browser_observer),
) -> TeachSessionResponse:
    """Retrieve the filtered event trace for a session."""
    session = _sessions.get(session_id)
    if session is None:
        raise NotFoundError("Session", session_id)

    events: list[BrowserEvent] = []
    try:
        events = observer.events(session_id)
    except NotImplementedError:
        pass

    return TeachSessionResponse(
        session_id=session_id,
        state=AgentState(session["state"]),
        message=f"{len(events)} event(s) captured.",
        event_count=len(events),
        events=events,
    )
