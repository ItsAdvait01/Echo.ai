"""Unit tests for the event model and collector. No browser needed."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.browser.collector import EventCollector
from app.browser.errors import ConsentRequiredError, InvalidSessionIdError, ObserverError
from app.browser.events import EventType, ObservedEvent, PageState, TargetDescriptor
from app.browser.observer import validate_session_id
from app.schemas.contracts import BrowserEvent


def _record_click(collector: EventCollector) -> None:
    collector.record(
        event_type=EventType.CLICK,
        page_id="page-1",
        target=TargetDescriptor(
            tag="button",
            role="button",
            accessible_name="Next lesson",
            visible_text="Next lesson",
            test_id="next-lesson",
            locator_hint='role=button[name="Next lesson"]',
        ),
        page_state=PageState(url="https://example.com/course", title="Python Course"),
    )


def test_event_serialization_matches_shared_contract() -> None:
    collector = EventCollector("session-a")
    _record_click(collector)

    contracts = collector.contracts()
    assert len(contracts) == 1
    event = contracts[0]

    # Shared A1 contract, validated strictly and JSON-serializable.
    assert isinstance(event, BrowserEvent)
    BrowserEvent.model_validate(event.model_dump())
    payload = json.loads(event.model_dump_json())
    assert set(payload) == {
        "id",
        "session_id",
        "timestamp",
        "event_type",
        "page_url",
        "page_title",
        "target_summary",
        "safe_value",
    }
    assert payload["event_type"] == "click"
    assert payload["target_summary"] == 'button name="Next lesson" testid=next-lesson'
    assert payload["page_url"] == "https://example.com/course"
    assert payload["safe_value"] is None


def test_events_have_unique_ids_and_ordered_sequence_numbers() -> None:
    collector = EventCollector("session-a")
    for _ in range(5):
        _record_click(collector)

    events = collector.snapshot()
    assert [event.sequence_number for event in events] == [1, 2, 3, 4, 5]
    assert len({event.event_id for event in events}) == 5


def test_timestamps_are_timezone_aware_iso8601_and_ordered() -> None:
    collector = EventCollector("session-a")
    for _ in range(3):
        _record_click(collector)

    events = collector.snapshot()
    stamps = [event.timestamp for event in events]
    assert all(stamp.tzinfo is not None for stamp in stamps)
    assert stamps == sorted(stamps)
    parsed = datetime.fromisoformat(json.loads(collector.contracts()[0].model_dump_json())["timestamp"])
    assert parsed.tzinfo is not None


def test_internal_model_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ObservedEvent(
            session_id="s",
            page_id="p",
            sequence_number=1,
            event_type=EventType.CLICK,
            unexpected_field="x",
        )


def test_bounded_buffer_drops_oldest_and_reports_loss() -> None:
    collector = EventCollector("session-a", max_events=3)
    for _ in range(5):
        _record_click(collector)

    stats = collector.stats()
    assert stats.buffered == 3
    assert stats.total_recorded == 5
    assert stats.dropped_events == 2
    assert [event.sequence_number for event in collector.snapshot()] == [3, 4, 5]


def test_sink_failure_is_counted_and_events_are_kept() -> None:
    delivered: list[ObservedEvent] = []

    def broken_sink(event: ObservedEvent) -> None:
        delivered.append(event)
        raise RuntimeError("downstream queue is down")

    collector = EventCollector("session-a", sink=broken_sink)
    _record_click(collector)

    stats = collector.stats()
    assert stats.sink_failures == 1
    assert len(delivered) == 1
    # The buffered trace is still authoritative for U2.
    assert len(collector.contracts()) == 1


def test_object_sink_with_emit_is_supported() -> None:
    class Sink:
        def __init__(self) -> None:
            self.received: list[ObservedEvent] = []

        def emit(self, event: ObservedEvent) -> None:
            self.received.append(event)

    sink = Sink()
    collector = EventCollector("session-a", sink=sink)
    _record_click(collector)
    assert len(sink.received) == 1
    assert collector.stats().sink_failures == 0


def test_sealed_collector_rejects_late_events() -> None:
    collector = EventCollector("session-a")
    collector.seal()
    assert collector.record(event_type=EventType.CLICK, page_id="p") is None
    assert collector.stats().late_events_dropped == 1
    assert collector.contracts() == []


def test_errors_are_structured_and_safe() -> None:
    error = ConsentRequiredError(details={"session_id": "s-1"})
    assert isinstance(error, ObserverError)
    payload = error.to_dict()
    assert payload["code"] == "consent_required"
    assert payload["retryable"] is False
    assert payload["message"]


def test_session_id_validation() -> None:
    assert validate_session_id("session-abc_123.4") == "session-abc_123.4"
    for bad in ("", " ../etc/passwd", "has space", "x" * 129):
        with pytest.raises(InvalidSessionIdError):
            validate_session_id(bad)
