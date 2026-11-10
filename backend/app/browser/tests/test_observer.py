"""Integration tests: real Chromium against local fixture pages only."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from support import first_event, trace_json, wait_for, wait_for_events

import app.browser.observer as observer_module
from app.browser.errors import (
    BrowserUnavailableError,
    ConsentRequiredError,
    DuplicateSessionError,
    InvalidSessionIdError,
    ObservationNotStartedError,
    ObserverError,
    SessionNotFoundError,
)
from app.browser.manager import ObservationSessionManager
from app.browser.observer import BrowserObserver, ObservationState
from app.schemas.contracts import BrowserEvent


def status_when(
    observer: BrowserObserver,
    predicate: Callable[[Any], bool],
    *,
    timeout: float = 10.0,
):
    """Poll the observer until ``predicate(status)`` holds, then return the status."""

    def check() -> Any:
        status = observer.status()
        return status if predicate(status) else None

    return wait_for(check, timeout=timeout)


# ---------------------------------------------------------------------------
# lifecycle and consent
# ---------------------------------------------------------------------------
def test_browser_session_starts_and_stops(
    observer_factory, fixture_page_url: str
) -> None:
    observer = observer_factory()
    status = observer.start(consent=True, url=fixture_page_url)

    assert status.state is ObservationState.OBSERVING
    assert status.session_id == observer.session_id
    assert status.started_at is not None
    assert status.stopped_at is None

    stopped = observer.stop()
    assert stopped.state is ObservationState.COMPLETED
    assert stopped.already_stopped is False
    assert stopped.stop_reason == "user_requested"
    assert observer.resources_released is True


def test_observation_cannot_start_without_consent(observer_factory) -> None:
    observer = observer_factory()
    with pytest.raises(ConsentRequiredError) as info:
        observer.start(consent=False)

    assert info.value.code == "consent_required"
    assert info.value.retryable is False
    assert observer.state is ObservationState.CONSENT_REQUIRED
    # Nothing was launched and no trace exists.
    assert observer.resources_released is True
    with pytest.raises(ObservationNotStartedError):
        observer.events()


def test_stop_before_start_is_a_structured_error(observer_factory) -> None:
    observer = observer_factory()
    with pytest.raises(ObservationNotStartedError) as info:
        observer.stop()
    assert info.value.code == "observation_not_started"
    assert observer.state is ObservationState.IDLE


def test_duplicate_start_is_rejected(live_observer: BrowserObserver) -> None:
    with pytest.raises(DuplicateSessionError) as info:
        live_observer.start(consent=True)
    assert info.value.code == "session_already_observing"
    assert live_observer.is_active is True


def test_duplicate_stop_is_idempotent(live_observer: BrowserObserver) -> None:
    first = live_observer.stop()
    second = live_observer.stop()
    third = live_observer.stop(reason="again")

    assert first.state is ObservationState.COMPLETED
    assert second.state is ObservationState.COMPLETED
    assert second.already_stopped is True
    assert third.already_stopped is True


def test_browser_unavailable_is_reported(observer_factory, monkeypatch) -> None:
    observer = observer_factory()
    monkeypatch.setattr(observer_module, "PLAYWRIGHT_AVAILABLE", False)

    with pytest.raises(BrowserUnavailableError) as info:
        observer.start(consent=True)

    assert info.value.code == "browser_unavailable"
    assert observer.state is ObservationState.FAILED
    assert observer.status().error is not None
    assert observer.resources_released is True


def test_launch_failure_leaves_a_safe_state(observer_factory, tmp_path) -> None:
    missing = tmp_path / "missing-chromium-binary"
    observer = observer_factory(launch_options={"executable_path": str(missing)})

    with pytest.raises(ObserverError) as info:
        observer.start(consent=True)

    assert info.value.code in {"browser_unavailable", "browser_launch_failed"}
    assert observer.state is ObservationState.FAILED
    assert observer.resources_released is True


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------
def test_navigation_event_is_captured(live_observer: BrowserObserver) -> None:
    event = first_event(live_observer, "navigation")

    assert event.page_state.url.endswith("/index.html")
    assert event.sequence_number >= 1
    assert event.session_id == live_observer.session_id
    assert event.source == "playwright_observer"


def test_page_load_event_carries_title(live_observer: BrowserObserver) -> None:
    event = first_event(live_observer, "page_load")

    assert event.page_state.url.endswith("/index.html")
    assert event.page_state.title == "Python Course Dashboard"


def test_click_event_is_captured(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.click("#next-lesson"))
    event = first_event(live_observer, "click")

    assert event.target.tag == "button"
    assert event.target.role == "button"
    assert event.target.accessible_name == "Next lesson"
    assert event.target.test_id == "next-lesson"
    assert event.target.locator_hint == 'role=button[name="Next lesson"]'
    assert event.page_state.url.endswith("/index.html")
    assert event.page_state.title == "Python Course Dashboard"


def test_link_navigation_strips_query_and_fragment(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.click("#next-link"))

    navigation = first_event(
        live_observer, "navigation", predicate=lambda e: e.page_state.url.endswith("/page2.html")
    )
    assert navigation.page_state.url_redacted is True
    assert navigation.sensitive_data_removed is True

    loaded = first_event(
        live_observer, "page_load", predicate=lambda e: e.page_state.url.endswith("/page2.html")
    )
    assert loaded.page_state.title == "Lesson 2 - Python Loops"

    trace = trace_json(live_observer)
    assert "supersecret123" not in trace
    assert "#lesson-2" not in trace
    assert "token=" not in trace


def test_text_input_is_captured_without_its_value(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.fill("#search", "python loops"))

    event = first_event(live_observer, "text_input")
    assert event.metadata["value_captured"] is False
    assert event.metadata["filled"] is True
    assert event.safe_value is None
    assert event.sensitive_data_removed is True
    assert event.target.field_category == "search"
    assert event.target.accessible_name == "Search lessons"
    assert event.target.locator_hint

    # The typed text appears nowhere in the detailed trace or the shared contract.
    assert "python loops" not in trace_json(live_observer)
    assert all(item.safe_value is None for item in live_observer.events())


def test_password_and_card_fields_are_excluded(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.fill("#password", "hunter2-secret"))
    live_observer.run_in_page(lambda page: page.fill("#card", "4111111111111111"))

    status = status_when(
        live_observer, lambda state: state.sensitive_interactions_suppressed >= 2
    )
    assert status.sensitive_interactions_suppressed == 2

    events = live_observer.snapshot_events()
    assert not any(str(event.event_type) == "text_input" for event in events)
    assert not any(event.target.field_category in {"password", "other"} for event in events)

    trace = trace_json(live_observer)
    assert "hunter2-secret" not in trace
    assert "4111111111111111" not in trace


def test_select_change_is_captured_with_safe_label(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.select_option("#level", label="Intermediate"))

    event = first_event(live_observer, "select_change")
    assert event.safe_value == "Intermediate"
    assert event.target.field_category == "select"
    assert event.metadata["selection_changed"] is True
    assert event.metadata["option_count"] == 3


def test_form_submit_is_observed_but_never_auto_submitted(
    live_observer: BrowserObserver,
) -> None:
    live_observer.run_in_page(lambda page: page.click("#submit-continue"))

    event = first_event(live_observer, "form_submit")
    assert event.metadata["values_captured"] is False
    assert event.metadata["auto_submitted"] is False
    assert event.metadata["action_url"].endswith("/page2.html")

    # Observation never submits on the user's behalf: we are still on page 1.
    assert live_observer.status().current_url.endswith("/index.html")


def test_key_presses_are_limited_to_navigation_keys(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.press("#search", "Enter"))

    event = first_event(live_observer, "key_press")
    assert event.metadata["key"] == "Enter"
    assert event.metadata["value_captured"] is False


def test_scroll_is_throttled_to_meaningful_movement(live_observer: BrowserObserver) -> None:
    def scroll(amount: int):
        async def action(page):
            await page.mouse.move(200, 300)
            await page.mouse.wheel(0, amount)

        return action

    live_observer.run_in_page(scroll(2000))
    first_event(live_observer, "scroll")

    for _ in range(6):
        live_observer.run_in_page(scroll(400))

    scrolls = [e for e in live_observer.snapshot_events() if str(e.event_type) == "scroll"]
    assert 1 <= len(scrolls) <= 3
    assert scrolls[0].metadata["direction"] == "down"
    assert scrolls[0].metadata["scroll_y"] > 0


def test_new_tab_is_recorded(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.click("#new-tab-link"))

    first_event(live_observer, "page_open")
    status = status_when(live_observer, lambda state: state.pages_observed >= 2)
    assert status.pages_observed >= 2


def test_page_close_is_handled_and_seals_the_session(
    observer_factory, fixture_page_url: str
) -> None:
    observer = observer_factory()
    observer.start(consent=True, url=fixture_page_url)

    observer.run_in_page(lambda page: page.close())

    terminal_states = (ObservationState.COMPLETED, ObservationState.FAILED)
    status = status_when(observer, lambda state: state.state in terminal_states, timeout=20.0)
    assert status.state is ObservationState.COMPLETED
    assert status.stop_reason == "page_closed"
    assert any(str(event.event_type) == "page_close" for event in observer.snapshot_events())
    assert observer.resources_released is True

    again = observer.stop()
    assert again.already_stopped is True


def test_stop_seals_the_event_stream(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.click("#next-lesson"))
    wait_for_events(live_observer, event_type="click")

    stopped = live_observer.stop()
    assert stopped.state is ObservationState.COMPLETED

    captured = len(live_observer.snapshot_events())
    assert captured == stopped.event_count
    with pytest.raises(ObserverError):
        live_observer.run_in_page(lambda page: page.click("#next-lesson"))
    assert len(live_observer.snapshot_events()) == captured


# ---------------------------------------------------------------------------
# guarantees
# ---------------------------------------------------------------------------
def test_events_are_unique_ordered_and_valid(live_observer: BrowserObserver) -> None:
    live_observer.run_in_page(lambda page: page.click("#next-lesson"))
    live_observer.run_in_page(lambda page: page.fill("#search", "python"))
    live_observer.run_in_page(lambda page: page.select_option("#level", label="Advanced"))
    wait_for_events(live_observer, minimum=5)

    events = live_observer.snapshot_events()
    assert len(events) == len({event.event_id for event in events})
    assert [event.sequence_number for event in events] == list(range(1, len(events) + 1))
    timestamps = [event.timestamp for event in events]
    assert all(stamp.tzinfo is not None for stamp in timestamps)
    assert timestamps == sorted(timestamps)

    for contract in live_observer.events():
        assert isinstance(contract, BrowserEvent)
        BrowserEvent.model_validate(contract.model_dump())
        json.loads(contract.model_dump_json())


def test_sink_failure_is_reported_and_trace_kept(
    observer_factory, fixture_page_url: str
) -> None:
    received: list[object] = []

    class Downstream:
        def emit(self, event: object) -> None:
            received.append(event)
            raise RuntimeError("downstream queue is unavailable")

    observer = observer_factory(sink=Downstream())
    observer.start(consent=True, url=fixture_page_url)
    observer.run_in_page(lambda page: page.click("#next-lesson"))
    wait_for_events(observer, event_type="click")

    status = observer.status()
    assert status.sink_failures >= 1
    assert len(received) >= 1
    assert any(str(event.event_type) == "click" for event in observer.snapshot_events())
    assert status.event_count == len(observer.snapshot_events())


# ---------------------------------------------------------------------------
# manager interface used by A1 / U2
# ---------------------------------------------------------------------------
def test_manager_rejects_unknown_and_invalid_sessions() -> None:
    manager = ObservationSessionManager(default_headless=True)
    with pytest.raises(SessionNotFoundError):
        manager.events("does-not-exist")
    with pytest.raises(InvalidSessionIdError):
        manager.start("../etc/passwd", consent=True)
    assert manager.has("does-not-exist") is False


def test_manager_exposes_the_u2_interface(
    fixture_page_url: str, browser_launch_options: dict
) -> None:
    manager = ObservationSessionManager(
        default_headless=True, launch_options=browser_launch_options
    )
    try:
        status = manager.start("demo-session", consent=True, url=fixture_page_url)
        assert status.state is ObservationState.OBSERVING

        manager.get("demo-session").run_in_page(lambda page: page.click("#next-lesson"))
        events = wait_for(lambda: manager.events("demo-session"))
        assert all(isinstance(event, BrowserEvent) for event in events)
        assert any(event.event_type == "click" for event in events)

        stopped = manager.stop("demo-session")
        assert stopped.state is ObservationState.COMPLETED
        assert manager.status("demo-session").state is ObservationState.COMPLETED

        with pytest.raises(DuplicateSessionError):
            manager.start("demo-session", consent=True)
    finally:
        manager.reset()
