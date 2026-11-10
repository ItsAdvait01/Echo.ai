"""Tests for the proposed observation router (wired locally, not via A1's app)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from support import wait_for

from app.browser.api import router
from app.browser.manager import ObservationSessionManager, get_session_manager


def _client(manager: ObservationSessionManager) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session_manager] = lambda: manager
    return TestClient(app)


def test_start_requires_explicit_consent() -> None:
    manager = ObservationSessionManager(default_headless=True)
    client = _client(manager)

    response = client.post("/api/learning/start", json={"consent": False})

    assert response.status_code == 400
    body = response.json()["detail"]
    assert body["code"] == "consent_required"
    assert body["retryable"] is False


def test_unknown_session_returns_404() -> None:
    manager = ObservationSessionManager(default_headless=True)
    client = _client(manager)

    assert client.get("/api/learning/nope/events").status_code == 404
    assert client.get("/api/learning/nope/status").status_code == 404
    assert client.post("/api/learning/nope/stop").status_code == 404


def test_start_stop_events_over_the_api(
    fixture_page_url: str, browser_launch_options: dict
) -> None:
    manager = ObservationSessionManager(
        default_headless=True, launch_options=browser_launch_options
    )
    client = _client(manager)
    try:
        started = client.post(
            "/api/learning/start",
            json={"session_id": "api-session", "consent": True, "url": fixture_page_url},
        )
        assert started.status_code == 200
        assert started.json()["state"] == "observing"

        manager.get("api-session").run_in_page(lambda page: page.click("#next-lesson"))
        wait_for(lambda: manager.events("api-session"))

        payload = client.get("/api/learning/api-session/events").json()
        assert payload["count"] >= 1
        expected_fields = {
            "id",
            "session_id",
            "timestamp",
            "event_type",
            "page_url",
            "page_title",
            "target_summary",
            "safe_value",
        }
        for event in payload["events"]:
            assert set(event) == expected_fields

        status = client.get("/api/learning/api-session/status").json()
        assert status["state"] == "observing"

        stopped = client.post("/api/learning/api-session/stop")
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "completed"

        stopped_again = client.post("/api/learning/api-session/stop")
        assert stopped_again.status_code == 200
        assert stopped_again.json()["already_stopped"] is True
    finally:
        manager.reset()
