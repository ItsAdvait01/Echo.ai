"""Controlled Playwright observer starter. U1 owns this module."""

from app.schemas.contracts import BrowserEvent


class BrowserObserver:
    """Observe only after an explicit task-scoped session is started."""

    def start(self, session_id: str) -> None:
        raise NotImplementedError("U1: initialize the authorized Playwright session")

    def stop(self, session_id: str) -> None:
        raise NotImplementedError("U1: stop capture immediately for this session")

    def events(self, session_id: str) -> list[BrowserEvent]:
        raise NotImplementedError("U1: return normalized, filtered events")
