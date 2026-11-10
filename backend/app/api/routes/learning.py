"""
Teach-mode (learning) routes.  Owned by A1.

Thin routing layer — delegates to U1 (observer) and U2 (learner) modules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.draft_adapter import normalize_draft
from app.api.routes.workflows import stage_draft
from app.browser.errors import ConsentRequiredError as BrowserConsentRequiredError
from app.browser.errors import DuplicateSessionError as BrowserDuplicateSessionError
from app.browser.errors import ObserverError
from app.browser.observer import BrowserObserver
from app.core.dependencies import (
    get_browser_observer,
    get_workflow_learner,
)
from app.core.errors import (
    ConsentRequiredError,
    EchoError,
    InvalidStateError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.learning.errors import WorkflowDraftError
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


def _observation_unavailable(exc: ObserverError) -> EchoError:
    """Map a U1 observer error onto the API's stable error contract."""
    if isinstance(exc, BrowserConsentRequiredError):
        return ConsentRequiredError()
    if isinstance(exc, BrowserDuplicateSessionError):
        return InvalidStateError("An observation session is already active.")
    # The controlled browser could not be opened or cleaned up: never claim
    # observation is live when it is not.
    return ServiceUnavailableError("browser observation")


@router.post("/start", response_model=TeachSessionResponse)
def start_teach_session(
    body: TeachStartRequest,
    observer: BrowserObserver = Depends(get_browser_observer),
) -> TeachSessionResponse:
    """Start a new observation session after explicit user consent."""
    if not body.consent_confirmed:
        raise ConsentRequiredError()

    session_id = uuid.uuid4().hex[:12]

    # Delegate observation to U1. Consent was confirmed by the caller, so the
    # observer may open its controlled browser at the requested page.
    try:
        observer.start(session_id, consent=True, url=body.target_url)
    except ObserverError as exc:
        raise _observation_unavailable(exc) from exc

    _sessions[session_id] = {
        "state": AgentState.LEARNING,
        "target_url": body.target_url,
        "started_at": datetime.now(UTC),
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

    # Delegate stop to U1 — seals the trace and releases the browser.
    try:
        observer.stop(session_id)
    except ObserverError as exc:
        raise _observation_unavailable(exc) from exc

    session["stopped"] = True
    session["state"] = AgentState.PROCESSING

    # Retrieve captured events from U1
    events: list[BrowserEvent] = []
    try:
        events = observer.events(session_id)
    except ObserverError as exc:
        raise _observation_unavailable(exc) from exc

    # Optionally generate draft via U2
    generate = True if body is None else body.generate_draft
    draft: WorkflowDraft | None = None
    draft_id: str | None = None
    draft_note = " No draft generated (no usable events were observed)."
    if generate and events:
        try:
            learned = learner.create_draft(events)
        except WorkflowDraftError as exc:
            # U2 refuses to invent a workflow from insufficient evidence.
            detail = getattr(exc, "message", None) or str(exc)
            session["state"] = AgentState.PROCESSING
            draft_note = f" No draft generated: {detail}"
        else:
            # Translate U2's proposal into U3/U4's executable vocabulary and
            # stage it for explicit review. Staging is never approval.
            draft = normalize_draft(learned, events)
            staged = stage_draft(
                name=draft.name,
                description=draft.description,
                trigger_phrases=list(draft.trigger_phrases),
                steps=list(draft.steps),
                uncertainty_notes=list(draft.uncertainty_notes),
            )
            draft_id = staged.id
            session["state"] = AgentState.AWAITING_APPROVAL
            draft_note = " Draft generated for review."

    session["draft_id"] = draft_id
    state = AgentState(session["state"])
    return TeachSessionResponse(
        session_id=session_id,
        state=state,
        message="Observation stopped." + draft_note,
        event_count=len(events),
        events=events,
        draft=draft,
        draft_id=draft_id,
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
    except ObserverError as exc:
        raise _observation_unavailable(exc) from exc

    return TeachSessionResponse(
        session_id=session_id,
        state=AgentState(session["state"]),
        message=f"{len(events)} event(s) captured.",
        event_count=len(events),
        events=events,
        draft_id=session.get("draft_id"),
    )
