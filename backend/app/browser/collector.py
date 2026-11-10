"""Ordered, bounded event collection. U1 owns this module.

One ``EventCollector`` per observation session. It is the only place where
sequence numbers, event ids and timestamps are assigned, which is what makes
ordering and uniqueness guarantees true by construction.

Design constraints from the spec (U1 Part 11/12/14):

* Never block the browser: appending is a locked list insert, sinks are called
  inline but guarded, and no sink is allowed to raise into the observer.
* Bounded memory: oldest events are dropped past ``max_events`` and the loss is
  reported instead of silently pretending capture was complete.
* A failing sink never loses buffered events - the in-memory buffer remains the
  authoritative trace that ``events()`` returns.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .events import EventType, ObservedEvent, PageState, TargetDescriptor, utc_now

DEFAULT_EVENT_LIMIT = 5000


@runtime_checkable
class EventSink(Protocol):
    """Delivery target for normalized events (queue, callback bridge, store)."""

    def emit(self, event: ObservedEvent) -> None: ...


SinkCallable = Callable[[ObservedEvent], None]


@dataclass
class CollectorStats:
    """Non-sensitive counters reported to callers and the API."""

    total_recorded: int = 0
    buffered: int = 0
    dropped_events: int = 0
    sink_failures: int = 0
    late_events_dropped: int = 0
    event_types: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_recorded": self.total_recorded,
            "buffered": self.buffered,
            "dropped_events": self.dropped_events,
            "sink_failures": self.sink_failures,
            "late_events_dropped": self.late_events_dropped,
            "event_types": dict(self.event_types),
        }


class EventCollector:
    """Assigns ordering and identity to events for a single session."""

    def __init__(
        self,
        session_id: str,
        *,
        max_events: int = DEFAULT_EVENT_LIMIT,
        sink: EventSink | SinkCallable | None = None,
    ) -> None:
        self.session_id = session_id
        self.max_events = max(1, int(max_events))
        self._sink = sink
        self._events: list[ObservedEvent] = []
        self._lock = threading.Lock()
        self._next_sequence = 1
        self._sealed = False
        self._total_recorded = 0
        self._dropped_events = 0
        self._sink_failures = 0
        self._late_events_dropped = 0
        self._event_types: dict[str, int] = {}

    # -- lifecycle ---------------------------------------------------------
    def seal(self) -> None:
        """Stop accepting events; later records are counted, not stored."""
        with self._lock:
            self._sealed = True

    @property
    def sealed(self) -> bool:
        return self._sealed

    # -- writing -----------------------------------------------------------
    def record(
        self,
        *,
        event_type: EventType,
        page_id: str,
        target: TargetDescriptor | None = None,
        page_state: PageState | None = None,
        metadata: dict[str, Any] | None = None,
        safe_value: str | None = None,
        sensitive_data_removed: bool = False,
        timestamp: Any = None,
    ) -> ObservedEvent | None:
        """Create, store and deliver one event. Returns ``None`` when sealed."""
        with self._lock:
            if self._sealed:
                self._late_events_dropped += 1
                return None
            sequence = self._next_sequence
            self._next_sequence += 1
            event = ObservedEvent(
                session_id=self.session_id,
                page_id=page_id,
                sequence_number=sequence,
                event_type=event_type,
                timestamp=timestamp or utc_now(),
                target=target or TargetDescriptor(),
                page_state=page_state or PageState(),
                metadata=dict(metadata or {}),
                safe_value=safe_value,
                sensitive_data_removed=sensitive_data_removed,
            )
            self._events.append(event)
            self._total_recorded += 1
            self._event_types[str(event_type)] = self._event_types.get(str(event_type), 0) + 1
            if len(self._events) > self.max_events:
                overflow = len(self._events) - self.max_events
                del self._events[:overflow]
                self._dropped_events += overflow

        self._deliver(event)
        return event

    def _deliver(self, event: ObservedEvent) -> None:
        """Best-effort sink delivery; failures are counted, never raised."""
        if self._sink is None:
            return
        try:
            emit = getattr(self._sink, "emit", None)
            if callable(emit):
                emit(event)
            elif callable(self._sink):
                self._sink(event)
            else:  # pragma: no cover - defensive
                raise TypeError("sink is neither callable nor has emit()")
        except Exception:  # noqa: BLE001 - a failing sink must never affect capture
            with self._lock:
                self._sink_failures += 1

    # -- reading -----------------------------------------------------------
    def snapshot(self) -> list[ObservedEvent]:
        """Copy of buffered events, already in sequence order."""
        with self._lock:
            return list(self._events)

    def contracts(self) -> list[Any]:
        """Buffered events projected onto the shared A1 contract."""
        return [event.to_contract() for event in self.snapshot()]

    def stats(self) -> CollectorStats:
        with self._lock:
            return CollectorStats(
                total_recorded=self._total_recorded,
                buffered=len(self._events),
                dropped_events=self._dropped_events,
                sink_failures=self._sink_failures,
                late_events_dropped=self._late_events_dropped,
                event_types=dict(self._event_types),
            )


__all__ = [
    "DEFAULT_EVENT_LIMIT",
    "CollectorStats",
    "EventCollector",
    "EventSink",
    "SinkCallable",
]
