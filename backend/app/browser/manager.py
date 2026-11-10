"""Observation session registry. U1 owns this module.

Holds at most one :class:`~app.browser.observer.BrowserObserver` per session id,
which is what prevents accidental duplicate sessions for the same id, and
exposes the small interface U2 and A1 consume:

``start`` / ``stop`` / ``events`` / ``status`` / ``sessions``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Mapping
from typing import Any

from app.schemas.contracts import BrowserEvent

from .collector import DEFAULT_EVENT_LIMIT, EventSink, SinkCallable
from .errors import (
    DuplicateSessionError,
    InvalidSessionIdError,
    SessionLimitError,
    SessionNotFoundError,
)
from .events import ObservedEvent
from .observer import BrowserObserver, ObservationState, ObservationStatus, validate_session_id

logger = logging.getLogger(__name__)

DEFAULT_MAX_SESSIONS = 4
_ACTIVE_STATES = (ObservationState.STARTING, ObservationState.OBSERVING)


class ObservationSessionManager:
    """Owns the lifecycle of every observation session in this process."""

    def __init__(
        self,
        *,
        event_limit: int = DEFAULT_EVENT_LIMIT,
        default_headless: bool = False,
        sink: EventSink | SinkCallable | None = None,
        launch_options: Mapping[str, Any] | None = None,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ) -> None:
        self._event_limit = event_limit
        self._default_headless = default_headless
        self._sink = sink
        self._launch_options = dict(launch_options or {})
        self._max_sessions = max(1, max_sessions)
        self._observers: dict[str, BrowserObserver] = {}
        self._lock = threading.RLock()

    # -- creation / lookup ------------------------------------------------
    def _new_observer(self, session_id: str) -> BrowserObserver:
        return BrowserObserver(
            session_id,
            event_limit=self._event_limit,
            sink=self._sink,
            headless=self._default_headless,
            launch_options=self._launch_options,
        )

    def create(self, session_id: str | None = None) -> BrowserObserver:
        """Return the observer for ``session_id``, creating one if needed."""
        sid = validate_session_id(session_id) if session_id else f"session-{uuid.uuid4()}"
        with self._lock:
            observer = self._observers.get(sid)
            if observer is None:
                if self._active_count() >= self._max_sessions:
                    raise SessionLimitError(details={"max_sessions": self._max_sessions})
                observer = self._new_observer(sid)
                self._observers[sid] = observer
            return observer

    def get(self, session_id: str) -> BrowserObserver:
        sid = validate_session_id(session_id)
        with self._lock:
            observer = self._observers.get(sid)
        if observer is None:
            raise SessionNotFoundError(details={"session_id": sid})
        return observer

    def has(self, session_id: str) -> bool:
        try:
            sid = validate_session_id(session_id)
        except InvalidSessionIdError:
            return False
        with self._lock:
            return sid in self._observers

    # -- interface consumed by A1 / U2 ------------------------------------
    def start(
        self,
        session_id: str | None = None,
        consent: bool = False,
        *,
        url: str | None = None,
    ) -> ObservationStatus:
        """Start observation for a session id after explicit consent."""
        sid = validate_session_id(session_id) if session_id else f"session-{uuid.uuid4()}"
        with self._lock:
            observer = self._observers.get(sid)
            if observer is None:
                if self._active_count() >= self._max_sessions:
                    raise SessionLimitError(details={"max_sessions": self._max_sessions})
                observer = self._new_observer(sid)
                self._observers[sid] = observer
            elif observer.is_active:
                raise DuplicateSessionError(details={"session_id": sid})
        return observer.start(consent=consent, url=url)

    def stop(
        self,
        session_id: str,
        *,
        reason: str = "user_requested",
    ) -> ObservationStatus:
        return self.get(session_id).stop(reason=reason)

    def status(self, session_id: str) -> ObservationStatus:
        return self.get(session_id).status()

    def events(self, session_id: str) -> list[BrowserEvent]:
        return self.get(session_id).events()

    def detail_events(self, session_id: str) -> list[ObservedEvent]:
        return self.get(session_id).snapshot_events()

    def sessions(self) -> list[ObservationStatus]:
        with self._lock:
            observers = list(self._observers.values())
        return [observer.status() for observer in observers]

    # -- lifecycle --------------------------------------------------------
    def shutdown(self, *, reason: str = "shutdown") -> None:
        """Stop every active session; used on application shutdown."""
        with self._lock:
            observers = list(self._observers.values())
        for observer in observers:
            if not observer.is_active:
                continue
            try:
                observer.stop(reason=reason)
            except Exception:  # noqa: BLE001 - shutdown is best effort
                logger.warning("session shutdown failed session=%s", observer.session_id)

    def reset(self, *, reason: str = "reset") -> None:
        """Stop all sessions and forget them (tests / local restarts)."""
        self.shutdown(reason=reason)
        with self._lock:
            self._observers.clear()

    def _active_count(self) -> int:
        return sum(1 for observer in self._observers.values() if observer.state in _ACTIVE_STATES)


_MANAGER: ObservationSessionManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_session_manager() -> ObservationSessionManager:
    """Process-wide manager (FastAPI dependency for the proposed router)."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ObservationSessionManager()
        return _MANAGER


def reset_session_manager() -> None:
    """Drop the process-wide manager; intended for tests."""
    global _MANAGER
    with _MANAGER_LOCK:
        manager = _MANAGER
        _MANAGER = None
    if manager is not None:
        manager.reset()


__all__ = [
    "DEFAULT_MAX_SESSIONS",
    "ObservationSessionManager",
    "get_session_manager",
    "reset_session_manager",
]
