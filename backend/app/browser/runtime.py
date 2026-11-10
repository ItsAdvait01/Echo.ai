"""Dedicated event loop that owns every Playwright object. U1 owns this module.

Playwright's async objects are bound to the loop that created them, while the
API layer may call the observer from a FastAPI threadpool (``def`` routes) or
from the main loop (``async def`` routes). Marshalling all browser work onto one
private loop keeps that consistent, keeps handlers firing immediately, and keeps
the public surface synchronous and safe to call from any thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from .errors import ObservationFailedError

T = TypeVar("T")


class AsyncRuntime:
    """A single background thread running a private asyncio event loop."""

    def __init__(self, *, name: str = "echo-u1-playwright") -> None:
        self._name = name
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self._ready = threading.Event()
        self._closed = False

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            with contextlib.suppress(Exception):
                self._loop.close()

    def start(self) -> AsyncRuntime:
        if not self._thread.is_alive() and not self._closed:
            self._thread.start()
        if not self._ready.wait(10.0):
            raise ObservationFailedError("The browser worker thread did not start.")
        return self

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._closed

    def run(self, coro: Coroutine[Any, Any, T], *, timeout: float | None = 30.0) -> T:
        """Run ``coro`` on the runtime loop and block only the calling thread."""
        if self._closed or not self._thread.is_alive():
            coro.close()
            raise ObservationFailedError("The browser runtime is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise ObservationFailedError("A browser operation timed out.") from exc
        except concurrent.futures.CancelledError as exc:  # pragma: no cover - defensive
            raise ObservationFailedError("A browser operation was cancelled.") from exc

    def shutdown(self, *, timeout: float = 10.0) -> None:
        """Idempotent loop shutdown; never raises."""
        if self._closed:
            return
        self._closed = True
        if not self._thread.is_alive():
            return
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:  # pragma: no cover - loop already closing
            return
        self._thread.join(timeout)


__all__ = ["AsyncRuntime"]
