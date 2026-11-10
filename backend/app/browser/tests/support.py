"""Shared helpers for the U1 observer tests (local fixtures only, no network)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FixtureRequestHandler(SimpleHTTPRequestHandler):
    """Serves the fixture directory; quiet so test output stays readable."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, *args: Any) -> None:
        """Silence access logs so test output stays readable."""
        return


def start_fixture_server() -> tuple[ThreadingHTTPServer, str]:
    """Start a loopback HTTP server for the fixture pages."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), FixtureRequestHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="u1-fixtures", daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


_LAUNCH_OPTIONS: dict[str, Any] | None = None


def probe_launch_options() -> dict[str, Any]:
    """Return launch options that actually start a browser on this host.

    Some managed Windows machines block Playwright's downloaded Chromium while
    allowing the installed Chrome/Edge, so the suite probes once per session and
    then exercises the same observer code with whichever browser can run.
    """
    global _LAUNCH_OPTIONS
    if _LAUNCH_OPTIONS is not None:
        return dict(_LAUNCH_OPTIONS)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        for candidate in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
            try:
                browser = playwright.chromium.launch(headless=True, **candidate)
            except Exception:  # noqa: BLE001, S112 - try the next browser candidate
                continue
            browser.close()
            _LAUNCH_OPTIONS = candidate
            return dict(candidate)
    raise RuntimeError("no launchable browser is available for the observer tests")


def wait_for(
    predicate: Callable[[], Any],
    *,
    timeout: float = 10.0,
    interval: float = 0.05,
    message: str = "condition was not met before the timeout",
) -> Any:
    """Poll ``predicate`` until it returns a truthy value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError(message)


def events_of(observer: Any) -> list[Any]:
    return observer.snapshot_events()


def first_event(observer: Any, event_type: str, *, predicate: Callable[[Any], bool] | None = None) -> Any:
    """Wait for the first event of ``event_type`` (optionally matching ``predicate``)."""

    def find() -> Any:
        for event in observer.snapshot_events():
            if str(event.event_type) != event_type:
                continue
            if predicate is not None and not predicate(event):
                continue
            return event
        return None

    return wait_for(find, message=f"no {event_type} event was captured")


def wait_for_events(observer: Any, *, event_type: str | None = None, minimum: int = 1) -> list[Any]:
    def find() -> Any:
        matched = [
            event
            for event in observer.snapshot_events()
            if event_type is None or str(event.event_type) == event_type
        ]
        return matched if len(matched) >= minimum else None

    return wait_for(find, message=f"expected at least {minimum} {event_type or 'any'} event(s)")


def trace_json(observer: Any) -> str:
    """Serialized full trace, used to prove no sensitive value leaked."""
    import json

    return json.dumps([event.to_dict() for event in observer.snapshot_events()])
