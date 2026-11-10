"""Phase A of the integration demo: Teach -> Learn -> Review -> Approve -> Save.

The API is served by a real uvicorn server over HTTP. The demonstration steps
are performed by this script acting as the *user* inside the very same
controlled browser session that U1 observes; capture, learning, review staging
and persistence are the real product path, not mocks.

Run from ``backend/`` with the demo site server already running:

    python scripts/demo_teach.py
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time

import httpx
import uvicorn

from app.api.routes.workflows import stage_draft
from app.core.dependencies import get_browser_observer
from app.main import app
from app.schemas.contracts import RiskLevel, WorkflowStep

API_PORT = int(os.getenv("ECHO_DEMO_PORT", "8010"))
SITE = os.getenv("ECHO_DEMO_SITE", "http://127.0.0.1:8765/index.html")
IDS_PATH = os.getenv("ECHO_DEMO_IDS") or os.path.join(
    tempfile.gettempdir(), "echo_demo_ids.json"
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    if not condition:
        failures.append(label)


def wait_for_api(client: httpx.Client, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if client.get("/api/health", timeout=2.0).status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - startup polling
            time.sleep(0.3)
    return False


def main() -> int:
    config = uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{API_PORT}", timeout=180.0) as client:
            check("API health reachable", wait_for_api(client))

            # 1. Consent is mandatory --------------------------------------
            no_consent = client.post(
                "/api/learning/start",
                json={"target_url": SITE, "consent_confirmed": False},
            )
            check(
                "teaching refused without consent",
                no_consent.status_code == 400
                and no_consent.json().get("code") == "consent_required",
                f"HTTP {no_consent.status_code} {no_consent.text[:90]}",
            )

            # 2. Start teaching at the selected URL ------------------------
            started = client.post(
                "/api/learning/start",
                json={"target_url": SITE, "consent_confirmed": True},
            )
            check(
                "teaching session starts at the selected URL",
                started.status_code == 200,
                f"HTTP {started.status_code} {started.text[:120]}",
            )
            session_id = started.json()["session_id"]
            print(f"         session_id={session_id}", flush=True)

            # 3. Demonstrate the task in the controlled browser ------------
            observer = get_browser_observer()
            observer.run_in_page(
                lambda page: page.wait_for_selector('[data-testid="next-lesson"]')
            )
            observer.run_in_page(lambda page: page.click('[data-testid="next-lesson"]'))
            observer.run_in_page(
                lambda page: page.wait_for_url("**/lesson2.html", timeout=15000)
            )
            time.sleep(0.4)

            events = client.get(f"/api/learning/{session_id}/events").json()
            event_types = sorted({e["event_type"] for e in events["events"]})
            check(
                "browser events captured during the demonstration",
                events["event_count"] >= 2,
                f"{events['event_count']} events, types={event_types}",
            )

            # 4. Stop observation and obtain a draft -----------------------
            stopped = client.post(
                f"/api/learning/{session_id}/stop", json={"generate_draft": True}
            )
            check("observation stops cleanly", stopped.status_code == 200)
            body = stopped.json()
            draft_id = body.get("draft_id")
            check(
                "stopped session returns a reviewable draft with an id",
                bool(draft_id),
                f"state={body.get('state')} message={body.get('message')!r}",
            )
            if draft_id:
                for step in body["draft"]["steps"]:
                    print(
                        f"         step {step['order']}: {step['action_type']} "
                        f"target={step['target'].get('test_id') or step['target'].get('name')!r} "
                        f"expected={step['expected_result'].get('type')} risk={step['risk']}",
                        flush=True,
                    )

            # 5. Review and approve ---------------------------------------
            approved = client.post(
                f"/api/workflows/{draft_id}/approve", json={"approved": True}
            )
            check(
                "draft is approved and persisted",
                approved.status_code == 200
                and approved.json()["workflow"]["approved"] is True,
                f"HTTP {approved.status_code} {approved.text[:90]}",
            )
            workflow_id = approved.json()["workflow"]["id"] if draft_id else None
            print(f"         workflow_id={workflow_id}", flush=True)

            # 6. High-risk fixture workflow (clearly NOT learned) ----------
            risky = stage_draft(
                name="Submit assignment (synthetic fixture)",
                description=(
                    "Integration fixture used only to exercise the explicit-"
                    "confirmation gate. It is not learned from the demonstration."
                ),
                trigger_phrases=["submit my assignment"],
                steps=[
                    WorkflowStep(
                        id="risky-1",
                        order=1,
                        action_type="click_semantic_target",
                        target={
                            "role": "button",
                            "name": "Submit assignment",
                            "test_id": "submit-assignment",
                            "url": SITE,
                        },
                        expected_result={
                            "type": "page_title",
                            "value": "Assignment submitted",
                            "match_mode": "contains",
                        },
                        risk=RiskLevel.HIGH,
                    )
                ],
                uncertainty_notes=["Synthetic fixture for the confirmation gate."],
            )
            risky_approved = client.post(
                f"/api/workflows/{risky.id}/approve", json={"approved": True}
            )
            check(
                "high-risk fixture workflow approved",
                risky_approved.status_code == 200,
            )

            payload = {
                "session_id": session_id,
                "draft_id": draft_id,
                "workflow_id": workflow_id,
                "risky_workflow_id": risky.id,
                "site": SITE,
                "failures": failures,
            }
            with open(IDS_PATH, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            print(f"         ids written to {IDS_PATH}", flush=True)
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print(f"\nPHASE_A_FAILURES={len(failures)}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
