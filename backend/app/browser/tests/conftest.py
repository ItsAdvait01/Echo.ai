"""Pytest fixtures for the U1 browser observer.

All browser tests use the local fixture server from ``support.py``. No external
site is required or contacted.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from support import probe_launch_options, start_fixture_server

from app.browser.observer import BrowserObserver


@pytest.fixture(scope="session")
def fixture_base_url() -> Iterator[str]:
    server, base_url = start_fixture_server()
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def fixture_page_url(fixture_base_url: str) -> str:
    return f"{fixture_base_url}/index.html"


@pytest.fixture(scope="session")
def browser_launch_options() -> dict[str, Any]:
    """Browser options that work on this host (probed once)."""
    return probe_launch_options()


@pytest.fixture()
def observer_factory(
    browser_launch_options: dict[str, Any],
) -> Iterator[Callable[..., BrowserObserver]]:
    """Build headless observers and always clean them up."""
    created: list[BrowserObserver] = []

    def factory(**kwargs: Any) -> BrowserObserver:
        kwargs.setdefault("headless", True)
        kwargs.setdefault("launch_options", dict(browser_launch_options))
        observer = BrowserObserver(**kwargs)
        created.append(observer)
        return observer

    yield factory

    for observer in created:
        if observer.is_active:
            with contextlib.suppress(Exception):
                observer.stop()


@pytest.fixture()
def live_observer(
    observer_factory: Callable[..., BrowserObserver], fixture_page_url: str
) -> Iterator[BrowserObserver]:
    """An observer already observing the fixture dashboard page."""
    observer = observer_factory()
    observer.start(consent=True, url=fixture_page_url)
    try:
        yield observer
    finally:
        if observer.is_active:
            observer.stop()
