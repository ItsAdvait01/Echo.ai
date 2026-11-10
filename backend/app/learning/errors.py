"""Structured errors for the U2 workflow learner pipeline."""

from typing import Any


class WorkflowDraftError(Exception):
    """Raised when a workflow draft cannot be produced from an event stream.

    Carries a stable machine-readable code, a user-readable message, and
    optional structured details so callers can surface review-friendly info.
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}
