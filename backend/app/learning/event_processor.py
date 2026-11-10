"""Event validation, ordering, de-duplication and session isolation.

Consumes the A1-owned ``BrowserEvent`` contract. Does not modify it.
"""

from typing import Any

from app.schemas.contracts import BrowserEvent
from app.learning.errors import WorkflowDraftError


class NormalizedEvents:
    """Result of event preprocessing: validated events plus a validation report."""

    def __init__(
        self,
        events: list[BrowserEvent],
        *,
        dropped_duplicates: list[str],
        session_ids: list[str],
        ordering: str,
        warnings: list[str],
    ) -> None:
        self.events = events
        self.dropped_duplicates = dropped_duplicates
        self.session_ids = session_ids
        self.ordering = ordering
        self.warnings = warnings

    @property
    def single_session(self) -> bool:
        return len(self.session_ids) <= 1


class EventProcessor:
    """Validates and normalizes a raw BrowserEvent stream.

    Required fields per the A1 contract: id, session_id, timestamp, event_type,
    page_url. Missing/empty required fields are rejected as malformed.
    """

    REQUIRED_FIELDS = ("id", "session_id", "timestamp", "event_type", "page_url")

    def process(self, events: list[Any]) -> NormalizedEvents:
        """Validate, de-duplicate, order and session-check the event stream.

        Raises :class:`WorkflowDraftError` with structured details when the
        stream cannot be safely processed (empty, malformed, mixed sessions).
        """
        if events is None:
            raise WorkflowDraftError(
                "empty_stream",
                "No events were provided for workflow learning.",
                {"count": 0},
            )
        if not isinstance(events, list) or len(events) == 0:
            raise WorkflowDraftError(
                "empty_stream",
                "The event stream is empty; cannot infer a workflow.",
                {"count": len(events) if isinstance(events, list) else 0},
            )

        malformed: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        duplicates: list[str] = []
        valid: list[BrowserEvent] = []
        session_ids: set[str] = set()
        warnings: list[str] = []

        for index, raw in enumerate(events):
            if not isinstance(raw, BrowserEvent):
                malformed.append(
                    {
                        "index": index,
                        "reason": "not_a_BrowserEvent",
                        "value": str(type(raw)),
                    }
                )
                continue
            missing = [field for field in self.REQUIRED_FIELDS if not getattr(raw, field, None)]
            if missing:
                malformed.append(
                    {
                        "index": index,
                        "event_id": getattr(raw, "id", None),
                        "missing_fields": missing,
                    }
                )
                continue
            if raw.id in seen_ids:
                duplicates.append(raw.id)
                continue
            seen_ids.add(raw.id)
            session_ids.add(raw.session_id)
            valid.append(raw)

        if duplicates:
            raise WorkflowDraftError(
                "duplicate_events",
                f"{len(duplicates)} duplicate event id(s) detected in the stream.",
                {
                    "duplicate_event_ids": duplicates,
                    "valid_count": len(valid),
                },
            )

        if malformed:
            raise WorkflowDraftError(
                "malformed_events",
                f"{len(malformed)} event(s) failed validation and were rejected.",
                {
                    "malformed": malformed,
                    "valid_count": len(valid),
                    "duplicate_event_ids": duplicates,
                },
            )

        if not valid:
            raise WorkflowDraftError(
                "no_valid_events",
                "No valid events remained after validation.",
                {
                    "duplicate_event_ids": duplicates,
                    "malformed_count": len(malformed),
                },
            )

        if len(session_ids) > 1:
            raise WorkflowDraftError(
                "mixed_sessions",
                "Events from multiple browser sessions were mixed in one stream.",
                {"session_ids": sorted(session_ids)},
            )

        # Order by timestamp, stable so equal timestamps keep input order.
        ordered = sorted(valid, key=lambda e: e.timestamp)

        return NormalizedEvents(
            events=ordered,
            dropped_duplicates=duplicates,
            session_ids=sorted(session_ids),
            ordering="by_timestamp",
            warnings=warnings,
        )
