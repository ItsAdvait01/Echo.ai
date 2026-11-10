"""Proposed observation API router. U1 owns this module.

``backend/app/api/**`` belongs to A1, so this router lives in U1's area and is
offered for wiring with one line in ``app/api/router.py``::

    from app.browser.api import router as learning_router
    api_router.include_router(learning_router, tags=["learning"])

The paths match TRD section 4.2 (``/api/learning/start``,
``/api/learning/{session_id}/stop``, ``/api/learning/{session_id}/events``) plus a
status read for A4 polling. Field names are a proposal to A1/Advait; the
observer itself does not depend on this router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .errors import ObserverError
from .manager import ObservationSessionManager, get_session_manager

router = APIRouter(prefix="/learning")

_HTTP_STATUS_BY_CODE: dict[str, int] = {
    "consent_required": 400,
    "invalid_session_id": 400,
    "session_not_found": 404,
    "session_already_observing": 409,
    "observation_not_started": 409,
    "too_many_sessions": 429,
    "browser_unavailable": 503,
    "browser_launch_failed": 502,
    "observation_failed": 502,
    "observer_cleanup_failed": 500,
}


class StartObservationRequest(BaseModel):
    """Body for ``POST /api/learning/start``."""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, max_length=128)
    consent: bool = Field(
        default=False,
        description="Must be true; observation never starts without explicit user authorization.",
    )
    url: str | None = Field(default=None, max_length=2048)


def _http_error(error: ObserverError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS_BY_CODE.get(error.code, 500),
        detail=error.to_dict(),
    )


@router.post("/start")
def start_observation(
    payload: StartObservationRequest,
    manager: ObservationSessionManager = Depends(get_session_manager),  # noqa: B008
) -> dict[str, Any]:
    """Start a controlled observation session after explicit consent."""
    try:
        status = manager.start(session_id=payload.session_id, consent=payload.consent, url=payload.url)
    except ObserverError as exc:
        raise _http_error(exc) from exc
    return status.to_dict()


@router.post("/{session_id}/stop")
def stop_observation(
    session_id: str,
    manager: ObservationSessionManager = Depends(get_session_manager),  # noqa: B008
) -> dict[str, Any]:
    """Stop capture immediately and seal the event trace."""
    try:
        status = manager.stop(session_id)
    except ObserverError as exc:
        raise _http_error(exc) from exc
    return status.to_dict()


@router.get("/{session_id}/events")
def get_observation_events(
    session_id: str,
    manager: ObservationSessionManager = Depends(get_session_manager),  # noqa: B008
) -> dict[str, Any]:
    """Return the normalized, sanitized trace that U2 interprets."""
    try:
        observer = manager.get(session_id)
        events = observer.events()
        status = observer.status()
    except ObserverError as exc:
        raise _http_error(exc) from exc
    return {
        "session_id": observer.session_id,
        "count": len(events),
        "events": [event.model_dump(mode="json") for event in events],
        "status": status.to_dict(),
    }


@router.get("/{session_id}/status")
def get_observation_status(
    session_id: str,
    manager: ObservationSessionManager = Depends(get_session_manager),  # noqa: B008
) -> dict[str, Any]:
    """Return lifecycle state and safe counters (A4 polls this)."""
    try:
        status = manager.status(session_id)
    except ObserverError as exc:
        raise _http_error(exc) from exc
    return status.to_dict()


__all__ = ["StartObservationRequest", "router"]
