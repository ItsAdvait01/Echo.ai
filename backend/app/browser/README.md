# U1 — Controlled Browser Observer

Observation layer for Echo.ai Teach Mode. It captures **what happened** in an
explicitly authorized Playwright session and hands normalized events to U2. It
never interprets intent, never generates actions and never executes anything.

Owner: U1. Exclusive write path: `backend/app/browser/**`.

## 1. Responsibilities

In scope: consent enforcement, controlled browser lifecycle, page/navigation/
interaction observation, event normalization and ordering, privacy sanitization,
bounded buffering, event delivery, structured errors and cleanup.

Out of scope: goal understanding, workflow drafting (U2), execution (U3),
verification (U4), API hosting (A1), UI (A4), voice (A3).

## 2. Architecture

```text
consent ──> BrowserObserver ──> AsyncRuntime (one private asyncio loop thread)
                                   └─ Playwright chromium ─> BrowserContext ─> Page
                                                                  │
        page events (framenavigated / load / close / crash) ──────┤
        in-page listeners ──> window.__echoEmit binding ──────────┤
                                                                  v
                                              EventSanitizer ─> EventCollector
                                                                  │  (id, sequence,
                                                                  │   timestamp, buffer)
                                                                  v
                                              BrowserEvent (shared A1 contract)
```

| Module | Responsibility |
|---|---|
| `observer.py` | `BrowserObserver`: lifecycle, consent, handlers, normalization |
| `runtime.py` | `AsyncRuntime`: the single loop thread that owns every Playwright object |
| `instrumentation.py` | The in-page script (clicks, inputs, selects, submits, keys, scroll) |
| `sanitizer.py` | URL/text/field sanitization and sensitive-field detection |
| `collector.py` | Ordered bounded buffer, sequence ids, sink delivery and counters |
| `events.py` | `ObservedEvent` model and projection onto the shared contract |
| `manager.py` | `ObservationSessionManager`: one observer per session id |
| `api.py` | Proposed FastAPI router (`/learning/...`) for A1 to wire |
| `errors.py` | Structured errors with stable codes |

Why a private loop thread: Playwright objects are bound to the loop that created
them, while FastAPI may call the observer from a threadpool. All browser work is
marshalled onto `AsyncRuntime`, so the public surface stays synchronous and safe
to call from any thread, and page handlers fire immediately without polling.

## 3. Lifecycle

```text
IDLE -> CONSENT_REQUIRED -> STARTING -> OBSERVING -> STOPPING -> COMPLETED
                                \-> FAILED <--------------------/
```

* Consent is required before a browser is launched (`start(consent=True)`).
* `stop()` seals the trace first, then closes only resources the observer owns.
* Stopping twice returns the prior status with `already_stopped=True`.
* Closing the observed page (or the browser) ends the session automatically with
  `stop_reason="page_closed"` / `"browser_disconnected"`; a renderer crash ends
  it as `FAILED` with `code="page_crashed"`.
* A failed start never stays in `STARTING`: it lands in `FAILED` with a
  structured error and releases resources.

## 4. Event types

| Type | Emitted when | Notable fields |
|---|---|---|
| `navigation` | Main-frame URL change (sanitized, deduplicated) | `page_state.url` |
| `page_load` | Document load completes | `page_state.title` |
| `click` | Any click | `target` descriptor |
| `text_input` | First input into a text-like control (one per field per page) | `metadata.filled`, `metadata.value_captured=false` |
| `select_change` | A `<select>` value is committed | `safe_value` (non-sensitive label only) |
| `form_submit` | User submits a form (never triggered by the observer) | `metadata.action_url`, `method`, `auto_submitted=false` |
| `key_press` | `Enter`, `Tab` or `Escape` only | `metadata.key` |
| `scroll` | Meaningful scroll (≥250 px, ≥750 ms apart) | `direction`, `scroll_y` |
| `page_open` | A new tab/page appears | `page_id` |
| `page_close` | A page closes | — |
| `page_crash` | Renderer crash | `metadata.reason` |

No event is emitted for credential or payment fields; those interactions are
counted in `status.sensitive_interactions_suppressed` instead.

## 5. Event schema

The published interface is A1's `app.schemas.contracts.BrowserEvent`
(`id, session_id, timestamp, event_type, page_url, page_title, target_summary,
safe_value`). `ObservedEvent.to_contract()` produces it; U2 consumes only this.

Internally `ObservedEvent` carries the richer evidence U1 owns: `page_id`,
`sequence_number`, `source`, `target` (tag, role, accessible name, visible text,
test id, locator hint, field category), `page_state`, `metadata` and
`sensitive_data_removed`.

**Change request to A1** (not implemented, shared schema is A1-owned): add
optional fields to `BrowserEvent` so U2 can use ordering and targets structurally
instead of parsing `target_summary`:

```text
page_id: str | None = None
sequence_number: int | None = None
target: dict[str, Any] = {}
metadata: dict[str, Any] = {}
sensitive_data_removed: bool = True
```

All of these are already populated internally; the projection simply drops them
today. Additive defaults keep existing callers working.

## 6. Interface

```python
from app.browser.manager import ObservationSessionManager

manager = ObservationSessionManager()                    # or get_session_manager()
manager.start(session_id, consent=True, url="https://…") # -> ObservationStatus
manager.status(session_id)                               # -> ObservationStatus
manager.events(session_id)                               # -> list[BrowserEvent]
manager.stop(session_id)                                 # -> ObservationStatus
manager.shutdown()                                       # app shutdown
```

Direct use:

```python
from app.browser.observer import BrowserObserver

observer = BrowserObserver()                      # headless=False for a demo
observer.start(consent=True, url="https://example.com/course")
observer.stop()
for event in observer.events():
    ...
observer.snapshot_events()                        # U1-internal detail trace
observer.run_in_page(lambda page: page.click("a")) # diagnostics/tests only
```

Errors are `ObserverError` subclasses with `code`, `message`, `retryable`:

| Code | Meaning |
|---|---|
| `consent_required` | Start attempted without consent |
| `session_not_found` / `invalid_session_id` | Unknown or malformed session id |
| `session_already_observing` | Duplicate start / reused session id |
| `observation_not_started` | Stop or read before start |
| `browser_unavailable` | No usable browser could be started |
| `browser_launch_failed` | Launch failed for a non-environment reason |
| `observation_failed` | Browser operation failed mid-session |
| `too_many_sessions` | Concurrent session cap reached |

### Proposed HTTP routes (`app/browser/api.py`)

A1 wires one line in `app/api/router.py`:

```python
from app.browser.api import router as learning_router
api_router.include_router(learning_router, tags=["learning"])
```

| Method + path | Purpose |
|---|---|
| `POST /api/learning/start` | Body `{session_id?, consent, url?}`; `consent` must be true |
| `POST /api/learning/{session_id}/stop` | Stop and seal; idempotent |
| `GET /api/learning/{session_id}/events` | `{session_id, count, events[], status}` |
| `GET /api/learning/{session_id}/status` | Lifecycle state and safe counters |

## 7. Privacy guarantees

1. No observation without `consent=True`; stop is immediate and seals the trace.
2. Typed values are never read into Python: only `filled: true|false` leaves the page.
3. Password / OTP / payment / auth / token fields are detected in JS and Python;
   their values are never read and no event is emitted for them.
4. URLs keep scheme, host, port and path; query strings, fragments and userinfo
   are dropped, and token-shaped path segments become `{token}`.
5. No full-page HTML, DOM dumps, screenshots, clipboard, keystroke values, or
   events from other tabs/contexts. Only the controlled context is observed.
6. Page payloads are untrusted: field names are whitelisted and strings
   re-sanitized, so a hostile page cannot inject arbitrary data.
7. No typed value, token or page content ever appears in errors or logs.

## 8. Setup

Dependencies are already declared by A1 in `backend/pyproject.toml`
(`playwright`, `pytest`, `httpx`). No manifest change was needed.

```bash
cd backend
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m playwright install chromium
```

**Managed-host note.** On hosts where endpoint security blocks Playwright's
downloaded Chromium (`spawn UNKNOWN` / `Permission denied`), the observer falls
back to the installed Chrome then Edge channel automatically (bounded to three
attempts). `status.browser_channel` reports which was used. To pin it explicitly:

```python
BrowserObserver(launch_options={"channel": "chrome"})
```

## 9. Testing

```bash
cd backend
.venv/Scripts/python -m pytest app/browser/tests -q
```

All browser tests use a loopback `http.server` serving
`tests/fixtures/index.html` and `page2.html`; no external site is contacted.
Coverage: consent enforcement, lifecycle, duplicate start/stop, navigation,
click, text input without value, password/card exclusion, URL query
sanitization, select, form submit, key press, scroll throttling, new tab, page
close handling, unique ids, ordering, timestamps, contract serialization, sink
failure reporting, launch failure, cleanup, manager and router behaviour.

The suite probes which browser can launch on the host and uses it, so the same
observer code is exercised on CI and on locked-down machines.

## 10. Known limitations

* Typed text is **not** retained (deliberate). U2 can see that a field was
  filled but not what was typed; workflows that replay text need an explicit,
  separately consented capture policy agreed by Advait/A1.
* Element descriptors are evidence, not replay selectors. Replaying a step is
  U3's problem and may require re-resolution.
* One `text_input` event per field per page visit (noise control); later edits
  to the same field are not re-emitted.
* Checkbox/radio toggles appear as `click` events only.
* Page listeners are re-attached on every load because `document.open()`
  (document.write, Playwright `set_content`) drops them; events fired in the
  few milliseconds between that replacement and re-attachment can be missed.
* File-chooser dialogs, iframes' inner interactions (the script runs in all
  frames but `page_id` is per page), drag-and-drop and canvas gestures are not
  modelled.
* Sessions are in-memory: they do not survive a process restart, and no
  screenshots or video are recorded.
* Attaching to a browser/context owned by another component is not supported;
  U1 only observes sessions it launches itself.
* A bounded buffer (default 5000 events) drops the oldest events and reports the
  loss in `status.dropped_events` rather than growing without limit.

## 11. Integration instructions

**For U2 (learner).** Call `manager.events(session_id)` after `stop` (or poll
while observing) and consume `BrowserEvent`. `event_type` is one of the types in
§4. `target_summary` is a compact descriptor such as
`button name="Next lesson" testid=next-lesson`. Treat `safe_value` as an optional
sanitized label that is only present for non-sensitive selects. Preserve the
list order (U1 assigns `sequence_number` in capture order). Do not expect typed
text; surface that as uncertainty in the draft.

**For A1 (API).** Wire `app/browser/api.py` as shown in §6 and call
`manager.shutdown()` on app shutdown. Map `ObserverError.code` to HTTP using
`api._STATUS_BY_CODE`. If the wider event fields in §5 are approved, the
projection in `events.py` is the one place to change.

**For A4 (UI).** Poll `GET /api/learning/{id}/status` for state, event count and
`sensitive_interactions_suppressed`; it is safe to display verbatim.

**For U3/U4.** U1 does not hand over a browser session. Execution and
verification own their own Playwright session; U1 supplies only observed
evidence.
