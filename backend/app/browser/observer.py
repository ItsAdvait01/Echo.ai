"""Controlled Playwright browser observation engine. U1 owns this module.

One :class:`BrowserObserver` owns one authorized, task-scoped browser session.
It captures *what happened* - navigation, clicks, safe input metadata, select
changes, form submits, key presses, meaningful scrolls and page lifecycle - and
never interprets intent. Interpretation belongs to U2.

Lifecycle (spec U1 Part 5)::

    IDLE -> CONSENT_REQUIRED -> STARTING -> OBSERVING -> STOPPING -> COMPLETED
                                     \\-> FAILED <-----------------/

Guarantees this module is responsible for:

* Explicit consent is required before a browser is launched.
* Every emitted event has a unique id, a monotonic sequence number, a
  timezone-aware timestamp and a sanitized payload.
* No typed value, password, token or payment datum ever leaves the page.
* Cleanup is idempotent, and a second ``stop()`` never raises.
* Failures return structured :class:`~app.browser.errors.ObserverError` values
  instead of leaving a session stuck in ``STARTING``.

Broad ``except Exception`` guards in this module are intentional: a page, a sink
or a teardown step must never be able to raise back into the observed browser or
into the reading API. Every such guard either counts the failure or logs it.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urljoin

from app.schemas.contracts import BrowserEvent

from .collector import (
    DEFAULT_EVENT_LIMIT,
    CollectorStats,
    EventCollector,
    EventSink,
    SinkCallable,
)
from .errors import (
    BrowserLaunchError,
    BrowserUnavailableError,
    ConsentRequiredError,
    DuplicateSessionError,
    InvalidSessionIdError,
    ObservationFailedError,
    ObservationNotStartedError,
    ObserverCleanupError,
    ObserverError,
)
from .events import EventType, ObservedEvent, PageState, TargetDescriptor, utc_now
from .instrumentation import BINDING_NAME, INIT_SCRIPT
from .runtime import AsyncRuntime
from .sanitizer import EventSanitizer

try:  # pragma: no cover - import guard is exercised by the unavailable-browser test
    from playwright.async_api import async_playwright
except Exception:  # noqa: BLE001 - a missing Playwright must not break the API import
    async_playwright = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

PLAYWRIGHT_AVAILABLE = async_playwright is not None

LAUNCH_TIMEOUT_S = 45.0
NAVIGATION_TIMEOUT_S = 25.0
STOP_TIMEOUT_S = 20.0
PAGE_ACTION_TIMEOUT_S = 30.0
CLOSE_TIMEOUT_S = 10.0
TITLE_FETCH_TIMEOUT_S = 2.0

ALLOWED_KEY_PRESSES = frozenset({"Enter", "Tab", "Escape"})

# Signatures of a host-level launch problem (blocked or missing browser binary)
# rather than a bug in the requested options.
ENVIRONMENT_LAUNCH_MARKERS = (
    "spawn UNKNOWN",
    "Executable doesn't exist",
    "executable doesn't exist",
    "playwright install",
)

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _resolve_relative(base_url: str, candidate: str) -> str:
    """Resolve a page-relative reference (e.g. a form action) against a page URL."""
    if not candidate:
        return ""
    if not base_url:
        return candidate
    try:
        return urljoin(base_url, candidate)
    except (TypeError, ValueError):
        return candidate


def validate_session_id(value: object) -> str:
    """Return a normalized session id or raise :class:`InvalidSessionIdError`."""
    text = str(value if value is not None else "").strip()
    if not _SESSION_ID_PATTERN.match(text):
        raise InvalidSessionIdError()
    return text


class ObservationState(StrEnum):
    """Lifecycle state of a single observation session."""

    IDLE = "idle"
    CONSENT_REQUIRED = "consent_required"
    STARTING = "starting"
    OBSERVING = "observing"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ObservationStatus:
    """Non-sensitive status snapshot safe to return over the API."""

    session_id: str
    state: ObservationState
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    pages_observed: int = 0
    event_count: int = 0
    total_events_recorded: int = 0
    dropped_events: int = 0
    sink_failures: int = 0
    sensitive_interactions_suppressed: int = 0
    normalization_failures: int = 0
    current_url: str = ""
    stop_reason: str | None = None
    browser_channel: str | None = None
    error: dict[str, Any] | None = None
    already_stopped: bool = False
    event_types: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": str(self.state),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "pages_observed": self.pages_observed,
            "event_count": self.event_count,
            "total_events_recorded": self.total_events_recorded,
            "dropped_events": self.dropped_events,
            "sink_failures": self.sink_failures,
            "sensitive_interactions_suppressed": self.sensitive_interactions_suppressed,
            "normalization_failures": self.normalization_failures,
            "current_url": self.current_url,
            "stop_reason": self.stop_reason,
            "browser_channel": self.browser_channel,
            "error": self.error,
            "already_stopped": self.already_stopped,
            "event_types": dict(self.event_types),
        }


class BrowserObserver:
    """Observe only after an explicit task-scoped session is started."""

    def __init__(
        self,
        session_id: str | None = None,
        *,
        event_limit: int = DEFAULT_EVENT_LIMIT,
        sink: EventSink | SinkCallable | None = None,
        sanitizer: EventSanitizer | None = None,
        headless: bool = False,
        launch_options: Mapping[str, Any] | None = None,
        launch_timeout_s: float = LAUNCH_TIMEOUT_S,
    ) -> None:
        self._session_id_explicit = session_id is not None
        self.session_id = validate_session_id(session_id) if session_id else f"session-{uuid.uuid4()}"
        self._event_limit = event_limit
        self._sink = sink
        self._sanitizer = sanitizer or EventSanitizer()
        self._headless = headless
        self._launch_options: dict[str, Any] = dict(launch_options or {})
        self._launch_timeout_s = launch_timeout_s

        self._lock = threading.RLock()
        self._state = ObservationState.IDLE
        self._runtime: AsyncRuntime | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._collector: EventCollector | None = None

        self._page_ids: dict[int, str] = {}
        self._open_pages: set[int] = set()
        self._last_nav_url: dict[int, str] = {}
        self._handlers: list[tuple[Any, str, Callable[..., Any]]] = []
        self._tasks: set[asyncio.Task[Any]] = set()

        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._stop_reason: str | None = None
        self._error: dict[str, Any] | None = None
        self._suppressed = 0
        self._normalization_failures = 0
        self._current_url = ""
        self._browser_channel: str | None = None
        self._owns_browser = False
        self._closing = False
        self._autostop_pending = False

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    @property
    def state(self) -> ObservationState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state in (ObservationState.STARTING, ObservationState.OBSERVING)

    @property
    def resources_released(self) -> bool:
        """True when no owned Playwright object is still held."""
        return self._playwright is None and self._browser is None and self._context is None

    def start(
        self,
        session_id: str | None = None,
        consent: bool = False,
        *,
        url: str | None = None,
    ) -> ObservationStatus:
        """Start a controlled browser session after explicit consent."""
        self._adopt_session_id(session_id)
        with self._lock:
            if self._state in (
                ObservationState.STARTING,
                ObservationState.OBSERVING,
                ObservationState.STOPPING,
            ):
                raise DuplicateSessionError(
                    "This observation session is already active.", details={"session_id": self.session_id}
                )
            if self._state in (ObservationState.COMPLETED, ObservationState.FAILED) or (
                self._collector is not None
            ):
                raise DuplicateSessionError(
                    "This observation session already ran; start a new session id.",
                    details={"session_id": self.session_id},
                )
            if not consent:
                self._state = ObservationState.CONSENT_REQUIRED
                self._error = ConsentRequiredError().to_dict()
                raise ConsentRequiredError(details={"session_id": self.session_id})
            self._state = ObservationState.STARTING
            self._started_at = utc_now()
            self._stopped_at = None
            self._stop_reason = None
            self._error = None
            self._closing = False
            self._collector = EventCollector(
                self.session_id, max_events=self._event_limit, sink=self._sink
            )

        try:
            if not PLAYWRIGHT_AVAILABLE:
                raise BrowserUnavailableError()
            runtime = AsyncRuntime(name=f"echo-u1-{self.session_id[:16]}")
            runtime.start()
            self._runtime = runtime
            runtime.run(self._launch(url), timeout=self._launch_timeout_s)
        except ObserverError as exc:
            self._fail(exc)
            raise
        except Exception as exc:  # unexpected launch problem
            error = ObservationFailedError(
                f"Observer startup failed ({type(exc).__name__}).",
                details={"session_id": self.session_id},
            )
            self._fail(error)
            raise error from exc

        with self._lock:
            self._state = ObservationState.OBSERVING
        logger.info(
            "observation started session=%s headless=%s", self.session_id, self._headless
        )
        return self.status()

    def stop(
        self,
        session_id: str | None = None,
        *,
        reason: str = "user_requested",
        failed: bool = False,
    ) -> ObservationStatus:
        """Stop capture immediately, seal the trace and release owned resources.

        Idempotent: stopping an already stopped session returns the previous
        status with ``already_stopped=True`` instead of raising.
        """
        self._adopt_session_id(session_id)
        with self._lock:
            if self._state in (ObservationState.COMPLETED, ObservationState.FAILED):
                status = self.status()
                status.already_stopped = True
                return status
            if self._state in (ObservationState.IDLE, ObservationState.CONSENT_REQUIRED) and (
                self._collector is None
            ):
                raise ObservationNotStartedError(details={"session_id": self.session_id})
            self._state = ObservationState.STOPPING
            self._stopped_at = utc_now()
            self._stop_reason = reason
            collector = self._collector

        if collector is not None:
            collector.seal()
        self._closing = True

        runtime = self._runtime
        cleanup_error: Exception | None = None
        if runtime is not None:
            try:
                runtime.run(self._teardown(), timeout=STOP_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - state must leave STOPPING
                cleanup_error = exc
                logger.warning("observer cleanup incomplete session=%s", self.session_id)
            finally:
                try:
                    runtime.shutdown()
                except Exception:  # pragma: no cover - shutdown never raises
                    logger.debug("runtime shutdown issue", exc_info=True)

        with self._lock:
            self._runtime = None
            if failed:
                self._state = ObservationState.FAILED
                if self._error is None:
                    code = (
                        "browser_disconnected"
                        if reason == "browser_disconnected"
                        else "page_crashed"
                        if reason == "page_crashed"
                        else "observation_failed"
                    )
                    self._error = {
                        "code": code,
                        "message": f"Observation ended unexpectedly ({reason}).",
                        "retryable": True,
                    }
            else:
                self._state = ObservationState.COMPLETED
            if cleanup_error is not None and self._state is ObservationState.COMPLETED:
                self._error = ObserverCleanupError(
                    f"Observation stopped, but cleanup reported {type(cleanup_error).__name__}."
                ).to_dict()
        logger.info("observation stopped session=%s reason=%s", self.session_id, reason)
        return self.status()

    def events(self, session_id: str | None = None) -> list[BrowserEvent]:
        """Normalized, sanitized events projected onto the shared A1 contract."""
        self._adopt_session_id(session_id)
        collector = self._collector
        if collector is None:
            raise ObservationNotStartedError(details={"session_id": self.session_id})
        return collector.contracts()

    def snapshot_events(self, session_id: str | None = None) -> list[ObservedEvent]:
        """U1-internal detail trace (page id, sequence, metadata) for diagnostics."""
        self._adopt_session_id(session_id)
        collector = self._collector
        if collector is None:
            return []
        return collector.snapshot()

    def status(self, session_id: str | None = None) -> ObservationStatus:
        self._adopt_session_id(session_id)
        with self._lock:
            collector = self._collector
            stats: CollectorStats = collector.stats() if collector is not None else CollectorStats()
            return ObservationStatus(
                session_id=self.session_id,
                state=self._state,
                started_at=self._started_at,
                stopped_at=self._stopped_at,
                pages_observed=len(self._page_ids),
                event_count=stats.buffered,
                total_events_recorded=stats.total_recorded,
                dropped_events=stats.dropped_events,
                sink_failures=stats.sink_failures,
                sensitive_interactions_suppressed=self._suppressed,
                normalization_failures=self._normalization_failures,
                current_url=self._current_url,
                stop_reason=self._stop_reason,
                browser_channel=self._browser_channel,
                error=self._error,
                event_types=stats.event_types,
            )

    def run_in_page(
        self,
        action: Callable[[Any], Awaitable[Any]],
        *,
        timeout: float = PAGE_ACTION_TIMEOUT_S,
    ) -> Any:
        """Run an async Playwright action against the controlled page.

        Diagnostics/test helper only (it is how fixtures drive a demonstration).
        It is deliberately not part of the U2 event interface.
        """
        with self._lock:
            runtime, page, state = self._runtime, self._page, self._state
        if runtime is None or page is None:
            raise ObservationNotStartedError(details={"session_id": self.session_id})
        if state is not ObservationState.OBSERVING:
            raise ObservationFailedError("Observation is not active.", details={"state": str(state)})
        return runtime.run(action(page), timeout=timeout)

    # ------------------------------------------------------------------
    # startup / teardown
    # ------------------------------------------------------------------
    def _adopt_session_id(self, session_id: str | None) -> None:
        if not session_id or session_id == self.session_id:
            return
        with self._lock:
            if self._session_id_explicit or self._collector is not None:
                raise InvalidSessionIdError(
                    "The supplied session id does not match this observer.",
                    details={"session_id": self.session_id},
                )
            self.session_id = validate_session_id(session_id)

    async def _launch(self, url: str | None) -> None:
        try:
            self._playwright = await async_playwright().start()  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - environment problem
            raise BrowserUnavailableError(
                "Playwright could not start in this environment.",
                details={"reason": type(exc).__name__},
            ) from exc

        await self._launch_browser()
        self._owns_browser = True
        self._bind(self._browser, "disconnected", self._on_browser_disconnected)

        self._context = await self._browser.new_context()
        await self._context.expose_binding(BINDING_NAME, self._on_browser_payload)
        await self._context.add_init_script(INIT_SCRIPT)

        self._page = await self._context.new_page()
        self._register_page(self._page, emit_open=False)
        self._bind(self._context, "page", self._on_new_page)

        if url:
            try:
                await self._page.goto(
                    url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_S * 1000
                )
            except Exception as exc:
                safe_url, _ = self._sanitizer.sanitize_url(url)
                raise ObservationFailedError(
                    f"Could not open the observation start page ({safe_url or 'invalid url'}).",
                    details={"reason": type(exc).__name__},
                ) from exc

    def _launch_candidates(self) -> list[dict[str, Any]]:
        """Launch options to try, in order.

        Explicit options win outright. Otherwise, if the bundled Chromium build
        cannot be executed on this host (common on managed Windows machines that
        block downloaded binaries) the observer falls back to the installed
        Chrome and Edge channels, which use the same engine. The fallback is
        bounded to three attempts and never retries a genuine launch error.
        """
        if self._launch_options:
            return [dict(self._launch_options)]
        return [{}, {"channel": "chrome"}, {"channel": "msedge"}]

    async def _launch_browser(self) -> None:
        candidates = self._launch_candidates()
        for index, candidate in enumerate(candidates):
            kwargs: dict[str, Any] = {"headless": self._headless}
            kwargs.update(candidate)
            try:
                self._browser = await self._playwright.chromium.launch(**kwargs)
            except Exception as exc:
                message = str(exc)
                environment_problem = any(marker in message for marker in ENVIRONMENT_LAUNCH_MARKERS)
                if environment_problem and index < len(candidates) - 1:
                    logger.warning(
                        "browser launch attempt %s failed (%s); trying the next available browser",
                        index + 1,
                        type(exc).__name__,
                    )
                    continue
                if environment_problem:
                    raise BrowserUnavailableError(
                        "No usable browser could be started on this host. Install the Playwright "
                        "Chromium build, or pass launch_options such as {'channel': 'chrome'}.",
                        details={
                            "reason": type(exc).__name__,
                            "attempted": str(candidate.get("channel") or "bundled-chromium"),
                        },
                    ) from exc
                raise BrowserLaunchError(
                    "The controlled browser could not be launched.",
                    details={"reason": type(exc).__name__},
                ) from exc
            self._browser_channel = str(candidate.get("channel") or "bundled-chromium")
            return

    async def _teardown(self) -> None:
        self._closing = True
        self._detach_handlers()
        context, browser, playwright = self._context, self._browser, self._playwright
        owns_browser = self._owns_browser
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None

        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        self._tasks.clear()

        if owns_browser:
            if context is not None:
                try:
                    await asyncio.wait_for(context.close(), timeout=CLOSE_TIMEOUT_S)
                except Exception:  # pragma: no cover - already gone
                    logger.debug("context close failed", exc_info=True)
            if browser is not None:
                try:
                    await asyncio.wait_for(browser.close(), timeout=CLOSE_TIMEOUT_S)
                except Exception:  # pragma: no cover - already gone
                    logger.debug("browser close failed", exc_info=True)
        if playwright is not None:
            try:
                await asyncio.wait_for(playwright.stop(), timeout=CLOSE_TIMEOUT_S)
            except Exception:  # pragma: no cover - already gone
                logger.debug("playwright stop failed", exc_info=True)

    def _fail(self, error: ObserverError) -> None:
        """Leave a failed start in a safe, fully cleaned-up state."""
        with self._lock:
            self._state = ObservationState.FAILED
            self._error = error.to_dict()
            self._stopped_at = utc_now()
            self._stop_reason = "startup_failed"
            collector = self._collector
        if collector is not None:
            collector.seal()
        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            try:
                runtime.run(self._teardown(), timeout=STOP_TIMEOUT_S)
            except Exception:
                logger.debug("teardown after failed start incomplete", exc_info=True)
            finally:
                try:
                    runtime.shutdown()
                except Exception:  # noqa: BLE001, S110 - shutdown is best effort
                    pass

    # ------------------------------------------------------------------
    # page registration and handlers
    # ------------------------------------------------------------------
    def _bind(self, target: Any, event: str, handler: Callable[..., Any]) -> None:
        try:
            target.on(event, handler)
        except Exception:
            logger.debug("could not register %s listener", event, exc_info=True)
            return
        self._handlers.append((target, event, handler))

    def _detach_handlers(self) -> None:
        for target, event, handler in self._handlers:
            try:
                target.remove_listener(event, handler)
            except Exception:  # noqa: BLE001, S110 - target may already be closed
                pass
        self._handlers.clear()

    def _register_page(self, page: Any, *, emit_open: bool) -> str | None:
        key = id(page)
        with self._lock:
            if key in self._page_ids:
                return self._page_ids[key]
            page_id = str(uuid.uuid4())
            self._page_ids[key] = page_id
            self._open_pages.add(key)
        self._bind_page(page)
        if emit_open:
            url, redacted = self._sanitizer.sanitize_url(self._page_url(page))
            self._record(
                EventType.PAGE_OPEN,
                page_id,
                page_state=PageState(url=url, url_redacted=redacted),
                metadata={"opened_by": "page"},
                sensitive_data_removed=redacted,
            )
        return page_id

    def _bind_page(self, page: Any) -> None:
        self._bind(page, "framenavigated", self._on_frame_navigated)
        self._bind(page, "load", self._on_page_load)
        self._bind(page, "close", self._on_page_close)
        self._bind(page, "crash", functools.partial(self._on_page_crash, page))

    def _page_id_for(self, page: Any) -> str | None:
        key = id(page)
        with self._lock:
            page_id = self._page_ids.get(key)
        if page_id:
            return page_id
        return self._register_page(page, emit_open=True)

    def _page_url(self, page: Any) -> str:
        try:
            return str(page.url or "")
        except Exception:  # noqa: BLE001 - page may already be closing
            return ""

    def _on_new_page(self, page: Any) -> None:
        try:
            self._register_page(page, emit_open=True)
        except Exception:
            logger.debug("could not register new page", exc_info=True)

    def _on_frame_navigated(self, frame: Any) -> None:
        try:
            if frame.parent_frame is not None:
                return
            page = frame.page
            raw_url = frame.url
        except Exception:  # noqa: BLE001 - detached frames are not observable
            return
        if page is None:
            return
        page_id = self._page_id_for(page)
        if page_id is None:
            return
        url, redacted = self._sanitizer.sanitize_url(raw_url)
        if not url:
            return
        key = id(page)
        with self._lock:
            if self._last_nav_url.get(key) == url:
                return
            self._last_nav_url[key] = url
            self._current_url = url
        self._record(
            EventType.NAVIGATION,
            page_id,
            page_state=PageState(url=url, title="", url_redacted=redacted),
            metadata={"frame": "main"},
            sensitive_data_removed=redacted,
        )

    def _on_page_load(self, page: Any) -> None:
        self._schedule(self._emit_page_load(page))

    async def _emit_page_load(self, page: Any) -> None:
        title = ""
        try:
            title = await asyncio.wait_for(page.title(), timeout=TITLE_FETCH_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - a missing title must not drop the load event
            title = ""
        # Re-attach listeners after every load. ``document.open()`` (document.write,
        # Playwright's set_content) drops existing listeners while window state
        # survives, so a one-shot injection guard would silently stop capture.
        try:
            await asyncio.wait_for(page.evaluate(INIT_SCRIPT), timeout=TITLE_FETCH_TIMEOUT_S)
        except Exception:
            logger.debug("could not re-attach observers after load", exc_info=True)
        raw_url = self._page_url(page)
        if not raw_url:
            return
        page_id = self._page_id_for(page)
        if page_id is None:
            return
        url, redacted = self._sanitizer.sanitize_url(raw_url)
        safe_title = self._sanitizer.sanitize_title(title)
        with self._lock:
            self._current_url = url
            self._last_nav_url[id(page)] = url
        self._record(
            EventType.PAGE_LOAD,
            page_id,
            page_state=PageState(url=url, title=safe_title, url_redacted=redacted),
            sensitive_data_removed=redacted,
        )

    def _on_page_close(self, page: Any) -> None:
        key = id(page)
        with self._lock:
            page_id = self._page_ids.get(key)
        url, redacted = self._sanitizer.sanitize_url(self._page_url(page))
        if page_id is not None:
            self._record(
                EventType.PAGE_CLOSE,
                page_id,
                page_state=PageState(url=url, url_redacted=redacted),
                sensitive_data_removed=redacted,
            )
        with self._lock:
            self._open_pages.discard(key)
            remaining = len(self._open_pages)
        if remaining == 0 and not self._closing:
            self._request_autostop("page_closed")

    def _on_page_crash(self, page: Any, *_: Any) -> None:
        page_id = self._page_ids.get(id(page)) or "unknown"
        self._record(
            EventType.PAGE_CRASH,
            page_id,
            metadata={"reason": "renderer_crashed"},
        )
        if not self._closing:
            self._request_autostop("page_crashed", failed=True)

    def _on_browser_disconnected(self, *_: Any) -> None:
        if self._closing:
            return
        with self._lock:
            if self._state in (ObservationState.COMPLETED, ObservationState.FAILED):
                return
        self._request_autostop("browser_disconnected", failed=True)

    def _request_autostop(self, reason: str, *, failed: bool = False) -> None:
        """Stop from a helper thread: ``stop()`` must never run on the loop thread."""
        with self._lock:
            if self._state not in (ObservationState.STARTING, ObservationState.OBSERVING):
                return
            if self._autostop_pending:
                return
            self._autostop_pending = True

        def runner() -> None:
            try:
                self.stop(reason=reason, failed=failed)
            except ObserverError:
                logger.info("autostop skipped session=%s reason=%s", self.session_id, reason)
            except Exception:  # pragma: no cover - defensive
                logger.warning("autostop failed session=%s", self.session_id, exc_info=True)
            finally:
                with self._lock:
                    self._autostop_pending = False

        threading.Thread(
            target=runner,
            name=f"echo-u1-autostop-{self.session_id[:12]}",
            daemon=True,
        ).start()

    def _schedule(self, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_event_loop()
            task = loop.create_task(coro)  # type: ignore[arg-type]
        except RuntimeError:  # pragma: no cover - loop already closed
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # payload handling / normalization
    # ------------------------------------------------------------------
    async def _on_browser_payload(self, source: Any, *args: Any) -> None:
        try:
            payload = args[0] if args else None
            if not isinstance(payload, Mapping):
                return
            page = source.get("page") if isinstance(source, Mapping) else None
            if page is None:
                return
            page_id = self._page_id_for(page)
            if page_id is None:
                return
            self._handle_payload(page_id, payload)
        except Exception:
            with self._lock:
                self._normalization_failures += 1
            logger.debug("malformed browser payload ignored", exc_info=True)

    def _handle_payload(self, page_id: str, payload: Mapping[str, Any]) -> None:
        kind = self._sanitizer.safe_text(payload.get("type"), 32).lower()
        if kind == "sensitive_interaction":
            # The page told us it touched a credential field; count it, store nothing.
            with self._lock:
                self._suppressed += 1
            return

        url, url_redacted = self._sanitizer.sanitize_url(payload.get("url"))
        title = self._sanitizer.sanitize_title(payload.get("title"))
        page_state = PageState(url=url, title=title, url_redacted=url_redacted)
        if url:
            with self._lock:
                self._current_url = url

        raw_target = payload.get("target")
        target = self._build_target(raw_target) if isinstance(raw_target, Mapping) else TargetDescriptor()

        if kind == "click":
            self._record(
                EventType.CLICK,
                page_id,
                target=target,
                page_state=page_state,
                sensitive_data_removed=url_redacted,
            )
            return
        if kind == "text_input":
            if target.field_category is None or self._sanitizer.is_sensitive_hint(
                target.accessible_name, target.test_id
            ):
                with self._lock:
                    self._suppressed += 1
                return
            self._record(
                EventType.TEXT_INPUT,
                page_id,
                target=target,
                page_state=page_state,
                metadata={"value_captured": False, "filled": payload.get("filled") is True},
                sensitive_data_removed=True,
            )
            return
        if kind == "select_change":
            safe_value = self._sanitizer.safe_field_value("select", payload.get("option_label"))
            self._record(
                EventType.SELECT_CHANGE,
                page_id,
                target=target,
                page_state=page_state,
                metadata={
                    "selection_changed": True,
                    "option_count": self._as_int(payload.get("option_count")),
                    "value_captured": safe_value is not None,
                },
                safe_value=safe_value,
                sensitive_data_removed=url_redacted or safe_value is None,
            )
            return
        if kind == "form_submit":
            raw_action = self._sanitizer.safe_text(payload.get("action"), 2048)
            action_url, action_redacted = self._sanitizer.sanitize_url(
                _resolve_relative(url, raw_action)
            )
            method = self._sanitizer.safe_text(payload.get("method"), 12).lower()
            self._record(
                EventType.FORM_SUBMIT,
                page_id,
                target=target,
                page_state=page_state,
                metadata={
                    "action_url": action_url,
                    "method": method if method in {"get", "post"} else "",
                    "values_captured": False,
                    "auto_submitted": False,
                },
                sensitive_data_removed=url_redacted or action_redacted,
            )
            return
        if kind == "key_press":
            key = self._sanitizer.safe_text(payload.get("key"), 16)
            if key not in ALLOWED_KEY_PRESSES:
                return
            self._record(
                EventType.KEY_PRESS,
                page_id,
                target=target,
                page_state=page_state,
                metadata={"key": key, "value_captured": False},
                sensitive_data_removed=True,
            )
            return
        if kind == "scroll":
            direction = "down" if self._sanitizer.safe_text(payload.get("direction"), 8).lower() == "down" else "up"
            self._record(
                EventType.SCROLL,
                page_id,
                page_state=page_state,
                metadata={"direction": direction, "scroll_y": self._as_int(payload.get("scroll_y"))},
                sensitive_data_removed=url_redacted,
            )
            return
        logger.debug("ignoring unknown observer payload type %r", kind)

    def _build_target(self, payload: Mapping[str, Any]) -> TargetDescriptor:
        """Whitelist page-provided descriptor fields; page data is untrusted."""
        return TargetDescriptor(
            tag=self._sanitizer.sanitize_tag(payload.get("tag")) or "element",
            role=self._sanitizer.sanitize_tag(payload.get("role")) or None,
            accessible_name=self._sanitizer.safe_text(payload.get("accessible_name")) or None,
            visible_text=self._sanitizer.safe_text(payload.get("visible_text")) or None,
            test_id=self._sanitizer.sanitize_test_id(payload.get("test_id")) or None,
            locator_hint=self._sanitizer.sanitize_locator(payload.get("locator_hint")) or None,
            field_category=self._sanitizer.normalize_field_category(payload.get("field_category")),
        )

    def _record(
        self,
        event_type: EventType,
        page_id: str,
        *,
        target: TargetDescriptor | None = None,
        page_state: PageState | None = None,
        metadata: dict[str, Any] | None = None,
        safe_value: str | None = None,
        sensitive_data_removed: bool = False,
    ) -> ObservedEvent | None:
        collector = self._collector
        if collector is None:
            return None
        try:
            return collector.record(
                event_type=event_type,
                page_id=page_id,
                target=target,
                page_state=page_state,
                metadata=metadata,
                safe_value=safe_value,
                sensitive_data_removed=sensitive_data_removed,
            )
        except Exception:  # noqa: BLE001 - counted and dropped, never raised into the page
            with self._lock:
                self._normalization_failures += 1
            logger.warning("event normalization failed; dropped %s", event_type)
            return None

    @staticmethod
    def _as_int(value: Any, *, default: int = 0, upper: int = 10_000_000) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, min(upper, number))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"BrowserObserver(session_id={self.session_id!r}, state={self._state!s})"


__all__ = [
    "ALLOWED_KEY_PRESSES",
    "PLAYWRIGHT_AVAILABLE",
    "BrowserObserver",
    "ObservationState",
    "ObservationStatus",
    "validate_session_id",
]
