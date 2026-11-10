"""Structured browser-observer errors. U1 owns this module.

Every failure surfaces a stable machine ``code``, a safe user-facing
``message`` and a ``retryable`` hint so A1 can map observer failures onto HTTP
responses without knowing anything about Playwright.

Messages must stay safe: they may contain a session id, an error code or a
low-level launch reason, but never typed values, page content, credentials or
tokens. Callers that catch these errors get ``to_dict()`` for the API payload.
"""

from __future__ import annotations

from typing import Any


class ObserverError(Exception):
    """Base class for every browser-observer failure."""

    code: str = "observer_error"
    message: str = "Browser observation failed."
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or type(self).message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        # ``details`` is restricted to non-sensitive counters/identifiers.
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ConsentRequiredError(ObserverError):
    """Observation was attempted without an explicit consent flag."""

    code = "consent_required"
    message = "Observation requires explicit user consent before it can start."
    retryable = False


class SessionNotFoundError(ObserverError):
    """The requested observation session does not exist."""

    code = "session_not_found"
    message = "No observation session exists for that session id."
    retryable = False


class InvalidSessionIdError(ObserverError):
    """The supplied session id is unusable."""

    code = "invalid_session_id"
    message = "The session id must be 1-128 characters of letters, digits, '-', '_' or '.'."
    retryable = False


class DuplicateSessionError(ObserverError):
    """The session is already running or has already been used."""

    code = "session_already_observing"
    message = "This observation session is already active or already finished."
    retryable = False


class ObservationNotStartedError(ObserverError):
    """Stop/read was requested before observation ever started."""

    code = "observation_not_started"
    message = "Observation has not been started for this session."
    retryable = False


class BrowserUnavailableError(ObserverError):
    """Playwright or its Chromium build is missing from the environment."""

    code = "browser_unavailable"
    message = (
        "Playwright or its Chromium build is not installed. "
        "Run 'python -m playwright install chromium' in the backend environment."
    )
    retryable = False


class BrowserLaunchError(ObserverError):
    """The controlled browser could not be launched."""

    code = "browser_launch_failed"
    message = "The controlled browser could not be launched."
    retryable = True


class ObservationFailedError(ObserverError):
    """A browser operation failed while observation was active."""

    code = "observation_failed"
    message = "Browser observation failed."
    retryable = True


class ObserverCleanupError(ObserverError):
    """Resources could not be released completely."""

    code = "observer_cleanup_failed"
    message = "Observation stopped, but browser resources could not be fully released."
    retryable = True


class SessionLimitError(ObserverError):
    """Too many concurrent observation sessions."""

    code = "too_many_sessions"
    message = "The maximum number of concurrent observation sessions is already active."
    retryable = True


__all__ = [
    "BrowserLaunchError",
    "BrowserUnavailableError",
    "ConsentRequiredError",
    "DuplicateSessionError",
    "InvalidSessionIdError",
    "ObservationFailedError",
    "ObservationNotStartedError",
    "ObserverCleanupError",
    "ObserverError",
    "SessionLimitError",
    "SessionNotFoundError",
]
