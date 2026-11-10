"""Controlled-browser adapter for workflow execution.

U3 never creates its own browser: ``BrowserExecutor`` accepts an injected
``perform_action`` callback. This adapter is that callback. It drives U1's
owned Playwright session, performs only explicit semantic actions (never raw
coordinates, never generated code) and returns the observed page state that U4
verifies.

Ownership: U1 (`backend/app/browser/**`).

Design notes
------------
* One controlled browser per adapter instance, started on first use and closed
  by :meth:`close`. Execution reuses the same session across steps so a run
  behaves like a user continuing on the same page.
* ``perform`` never raises: browser problems are returned as an unsuccessful
  observation so U4 classifies the failure and the run pauses instead of
  crashing the request.
* Only allowlisted action types from ``BrowserExecutor.ALLOWED_ACTIONS`` are
  performed here as well; anything else is refused before touching the page.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.browser.observer import BrowserObserver

logger = logging.getLogger(__name__)

PAGE_ACTION_TIMEOUT_MS = 15_000
NAVIGATION_SETTLE_MS = 5_000
# How long each candidate locator is given to appear before the next is tried.
LOCATOR_PROBE_MS = 1_500
MAX_TEXT_CHARS = 2_000
MAX_VISIBLE_ELEMENTS = 25
ACTION_TIMEOUT_S = 45.0

NAVIGATE_ACTIONS = frozenset({"navigate_to_known_page"})
CLICK_ACTIONS = frozenset({"click_semantic_target", "open_next_incomplete_item"})
PASSIVE_ACTIONS = frozenset({"wait_for_visible_state", "read_progress_signal"})

_VISIBLE_ELEMENTS_JS = """
() => {
  const out = [];
  const nodes = document.querySelectorAll(
    'button, a, [role="button"], [role="link"], h1, h2, h3, [data-testid]'
  );
  for (const node of Array.from(nodes).slice(0, 120)) {
    const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text) { out.push(text.slice(0, 80)); }
    if (out.length >= 60) { break; }
  }
  return out;
}
"""


# HTML tag names and loose role words that U1 records, mapped onto the ARIA
# roles Playwright can actually address.
_ARIA_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "input": ("textbox", "searchbox", "combobox"),
    "textarea": ("textbox",),
    "textbox": ("textbox",),
    "search": ("searchbox", "textbox"),
    "searchbox": ("searchbox", "textbox"),
    "a": ("link",),
    "link": ("link",),
    "select": ("combobox",),
    "img": ("img",),
    "heading": ("heading",),
    "listitem": ("listitem",),
}


def _aria_roles(role: str) -> tuple[str, ...]:
    """Expand a recorded role into the ARIA roles worth trying."""
    key = role.strip().lower()
    if not key:
        return ()
    if key in _ARIA_ROLE_ALIASES:
        return _ARIA_ROLE_ALIASES[key]
    return (key,)


class ExecutionBrowserAdapter:
    """Perform allowlisted workflow actions in one controlled browser session."""

    def __init__(
        self,
        *,
        headless: bool | None = None,
        launch_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._observer: BrowserObserver | None = None
        self._headless = (
            headless
            if headless is not None
            else os.getenv("ECHO_BROWSER_HEADLESS", "1").strip().lower() not in {"0", "false", "no"}
        )
        self._launch_options = dict(launch_options) if launch_options else None

    # -- lifecycle -------------------------------------------------------

    @property
    def browser_channel(self) -> str | None:
        """Which browser build the current session launched with, if any."""
        with self._lock:
            observer = self._observer
        if observer is None:
            return None
        try:
            return observer.status().browser_channel
        except Exception:  # noqa: BLE001 - diagnostics only
            return None

    def is_active(self) -> bool:
        with self._lock:
            observer = self._observer
        return bool(observer is not None and observer.is_active)

    def close(self) -> None:
        """Stop the controlled session and release every browser resource."""
        with self._lock:
            observer, self._observer = self._observer, None
        if observer is None:
            return
        try:
            observer.stop(reason="execution_finished")
        except Exception:
            logger.warning("execution browser cleanup incomplete", exc_info=True)

    # -- BrowserExecutor callback ---------------------------------------

    def perform(self, action: dict[str, Any]) -> dict[str, Any]:
        """Perform one allowlisted action and return the observed page state."""
        action_type = str(action.get("action_type") or "")
        target = dict(action.get("target") or {})

        if action_type not in NAVIGATE_ACTIONS | CLICK_ACTIONS | PASSIVE_ACTIONS:
            return self._failure(f"unsupported_action:{action_type}")

        try:
            observer = self._ensure_session(target)
            observed = observer.run_in_page(
                lambda page: self._apply(page, action_type, target),
                timeout=ACTION_TIMEOUT_S,
            )
        except Exception as exc:
            logger.warning("execution action failed action_type=%s", action_type, exc_info=True)
            return self._failure(f"{type(exc).__name__}: {exc}")

        if not isinstance(observed, dict):
            return self._failure("adapter_returned_no_observation")
        return observed

    # -- internals -------------------------------------------------------

    def _ensure_session(self, target: Mapping[str, Any]) -> BrowserObserver:
        start_url = str(target.get("url") or target.get("page_url") or "").strip()
        with self._lock:
            observer = self._observer
            if observer is not None and observer.is_active:
                # The session is reused across runs, but it must be on the page
                # this step expects; otherwise the step runs against the page a
                # previous workflow left behind.
                current = str(observer.status().current_url or "").rstrip("/")
                if start_url and start_url.rstrip("/") != current:
                    logger.info("execution session navigating to %s", start_url)
                    try:
                        observer.run_in_page(
                            lambda page: self._apply(
                                page, "navigate_to_known_page", {"url": start_url}
                            ),
                            timeout=ACTION_TIMEOUT_S,
                        )
                    except Exception:
                        logger.warning(
                            "execution session could not reach the step start page",
                            exc_info=True,
                        )
                return observer
            if observer is not None:
                self._observer = None
            observer = BrowserObserver(
                headless=self._headless, launch_options=self._launch_options
            )
            # Consent here means "the user approved executing this workflow":
            # the workflow itself can only be executed after approval.
            observer.start(consent=True, url=target.get("url") or None)
            self._observer = observer
            logger.info("execution browser started channel=%s", observer.status().browser_channel)
            return observer

    async def _apply(self, page: Any, action_type: str, target: Mapping[str, Any]) -> dict[str, Any]:
        if action_type in NAVIGATE_ACTIONS:
            url = target.get("url") or target.get("page_url")
            if not url:
                raise ValueError("navigate action has no url")
            await page.goto(str(url), wait_until="domcontentloaded", timeout=PAGE_ACTION_TIMEOUT_MS)
            await self._settle(page)

        elif action_type in CLICK_ACTIONS:
            # Single-page apps render their controls after the document loads,
            # so give the page a bounded chance to settle before locating.
            await self._settle(page)
            locator = await self._locator(page, target)
            await locator.first.click(timeout=PAGE_ACTION_TIMEOUT_MS)
            await self._settle(page)

        elif action_type == "wait_for_visible_state":
            locator = await self._locator(page, target)
            await locator.first.wait_for(state="visible", timeout=PAGE_ACTION_TIMEOUT_MS)

        # read_progress_signal and every other action end with an observation.
        return await self._observe(page, action_type)

    async def _settle(self, page: Any) -> None:
        """Give a navigation triggered by the action a bounded chance to settle."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_SETTLE_MS)
        except Exception:
            logger.debug("page did not settle after action", exc_info=True)

    async def _locator(self, page: Any, target: Mapping[str, Any]) -> Any:
        """Resolve a semantic target to a Playwright locator.

        Recorded targets sometimes carry an HTML tag name (``input``) where
        Playwright expects an ARIA role, which can never match and only fails
        after the click times out. Every recorded role is therefore expanded
        into its real ARIA roles, and candidates are probed in order so an
        unlocatable first guess does not stall the action.
        """
        role = str(target.get("role") or "").strip()
        name = str(target.get("name") or "").strip()
        test_id = str(target.get("test_id") or "").strip()
        label = str(target.get("label") or "").strip()
        placeholder = str(target.get("placeholder") or "").strip()
        selector = str(target.get("selector") or "").strip()

        candidates: list[Any] = []
        if test_id:
            candidates.append(page.get_by_test_id(test_id))
        if name:
            candidates.extend(page.get_by_role(alias, name=name) for alias in _aria_roles(role))
        if label:
            candidates.append(page.get_by_label(label))
        if placeholder:
            candidates.append(page.get_by_placeholder(placeholder))
        if selector:
            candidates.append(page.locator(selector))
        if name:
            # Ambiguous by construction; U3 refuses such targets before this
            # point, so reaching here means the draft lacked a semantic target.
            candidates.append(page.get_by_role("button", name=name))
        candidates.extend(page.get_by_role(alias) for alias in _aria_roles(role))
        if not candidates:
            raise ValueError("target has no semantic locator")

        for candidate in candidates:
            try:
                await candidate.first.wait_for(state="attached", timeout=LOCATOR_PROBE_MS)
                return candidate
            except Exception:  # an invalid or absent candidate is just skipped
                logger.debug("locator candidate not present", exc_info=True)
        # Nothing matched yet: hand back the best guess so Playwright's own
        # timeout reports which locator actually failed.
        return candidates[0]

    async def _observe(self, page: Any, action_type: str) -> dict[str, Any]:
        """Collect the page state U4 needs to verify the step."""
        stamp = datetime.now(UTC).isoformat()
        title = ""
        text = ""
        visible: list[str] = []
        try:
            title = await page.title()
        except Exception:
            logger.debug("could not read page title", exc_info=True)
        try:
            text = await page.inner_text("body")
        except Exception:
            logger.debug("could not read body text", exc_info=True)
        try:
            visible = await page.evaluate(_VISIBLE_ELEMENTS_JS)
        except Exception:
            logger.debug("could not read visible elements", exc_info=True)

        return {
            "success": True,
            "action_type": action_type,
            "page_url": page.url,
            "page_title": title,
            # U4 comparisons are string based; the title is the stable
            # human-visible page state signal available without page content.
            "page_state": title,
            "text_content": str(text)[:MAX_TEXT_CHARS],
            "visible_elements": list(visible)[:MAX_VISIBLE_ELEMENTS],
            "navigation_completed": True,
            "timestamp": stamp,
            "action_result": {"success": True, "timestamp": stamp},
        }

    @staticmethod
    def _failure(reason: str) -> dict[str, Any]:
        stamp = datetime.now(UTC).isoformat()
        return {
            "success": False,
            "page_url": "",
            "page_title": "",
            "timestamp": stamp,
            "action_result": {"success": False, "error": reason, "timestamp": stamp},
        }


__all__ = ["ExecutionBrowserAdapter"]
