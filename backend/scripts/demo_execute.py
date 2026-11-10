"""Phase B of the integration demo: Retrieve -> Execute -> Verify.

Runs against an already-running API server (restarted after phase A, which is
what proves persistence). Every browser action here is performed by the real
controlled-browser adapter; nothing is simulated.

    python scripts/demo_execute.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import httpx

API = os.getenv("ECHO_DEMO_API", "http://127.0.0.1:8010")
IDS_PATH = os.getenv("ECHO_DEMO_IDS") or os.path.join(
    tempfile.gettempdir(), "echo_demo_ids.json"
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    if not condition:
        failures.append(label)


def main() -> int:
    with open(IDS_PATH, encoding="utf-8") as handle:
        ids = json.load(handle)

    workflow_id = ids["workflow_id"]
    risky_id = ids["risky_workflow_id"]

    with httpx.Client(base_url=API, timeout=180.0) as client:
        check("API restarted and healthy", client.get("/api/health").status_code == 200)

        # 1. Retrieve the approved workflow after the restart --------------
        listed = client.get("/api/workflows").json()
        check(
            "approved workflow persisted across the API restart",
            any(w["id"] == workflow_id for w in listed["workflows"]),
            f"{listed['count']} workflow(s) in the library",
        )
        fetched = client.get(f"/api/workflows/{workflow_id}")
        check("workflow retrievable by id", fetched.status_code == 200)
        steps = fetched.json()["workflow"]["steps"] if fetched.status_code == 200 else []
        for step in steps:
            print(
                f"         step {step['order']}: {step['action_type']} "
                f"target={step['target'].get('test_id') or step['target'].get('name')!r} "
                f"expected={step['expected_result'].get('type')}"
                f"={step['expected_result'].get('value')!r} risk={step['risk']}",
                flush=True,
            )

        # 2. High-risk learned step waits for explicit confirmation --------
        first = client.post("/api/executions", json={"workflow_id": workflow_id})
        first_body = first.json() if first.status_code == 200 else {}
        first_run = first_body.get("run", {})
        check(
            "high-risk step waits for explicit confirmation",
            first_run.get("state") == "waiting_for_confirmation"
            and first_run.get("last_verified_step", 0) == 0,
            f"state={first_run.get('state')} message={first_body.get('message')!r}",
        )

        # 3. Pause must not report completion ------------------------------
        first_id = first_run.get("id")
        if first_id:
            paused = client.post(f"/api/executions/{first_id}/pause")
            paused_run = paused.json()["run"] if paused.status_code == 200 else {}
            check(
                "pause reports paused and never advances progress",
                paused.status_code == 200
                and paused_run.get("state") == "paused"
                and paused_run.get("last_verified_step", 0) == 0,
                f"state={paused_run.get('state')}",
            )

        # 4. Confirmed step executes in the real browser and verifies ------
        second = client.post("/api/executions", json={"workflow_id": workflow_id})
        second_id = second.json()["run"]["id"] if second.status_code == 200 else None
        if second_id:
            confirmed = client.post(f"/api/executions/{second_id}/confirm")
            confirmed_body = confirmed.json() if confirmed.status_code == 200 else {}
            confirmed_run = confirmed_body.get("run", {})
            verification = confirmed_body.get("verification") or {}
            observed = verification.get("observed") or {}
            check(
                "confirmed step executes live and advances only on verification",
                confirmed.status_code == 200
                and confirmed_run.get("last_verified_step") == 1
                and verification.get("passed") is True,
                f"state={confirmed_run.get('state')} "
                f"passed={verification.get('passed')} "
                f"observed_url={observed.get('page_url')} "
                f"observed_title={observed.get('page_title')!r}",
            )

            # 5. Next bounded step runs without confirmation ---------------
            next_run = client.post(
                "/api/executions",
                json={"workflow_id": workflow_id, "resume_from_step": 1},
            )
            next_body = next_run.json() if next_run.status_code == 200 else {}
            next_state = next_body.get("run", {})
            next_verification = next_body.get("verification") or {}
            check(
                "next bounded step executes and completes the workflow",
                next_run.status_code == 200
                and next_state.get("state") == "completed"
                and next_state.get("last_verified_step") == 2,
                f"state={next_state.get('state')} "
                f"last_verified_step={next_state.get('last_verified_step')} "
                f"message={next_body.get('message')!r} "
                f"passed={(next_verification or {}).get('passed')}",
            )

            # 6. Verified checkpoints survive a later run ------------------
            again = client.post(
                "/api/executions",
                json={"workflow_id": workflow_id, "resume_from_step": 2},
            )
            again_run = again.json()["run"] if again.status_code == 200 else {}
            check(
                "previously verified checkpoints retained on a later run",
                again_run.get("last_verified_step") == 2
                and again_run.get("state") == "completed",
                f"state={again_run.get('state')} "
                f"last_verified_step={again_run.get('last_verified_step')}",
            )

        # 7. Synthetic high-risk fixture: gate, then safe stop -------------
        risky = client.post("/api/executions", json={"workflow_id": risky_id})
        risky_body = risky.json() if risky.status_code == 200 else {}
        risky_run = risky_body.get("run", {})
        check(
            "high-risk fixture stops at the confirmation gate",
            risky_run.get("state") == "waiting_for_confirmation"
            and risky_run.get("last_verified_step", 0) == 0,
            f"state={risky_run.get('state')} message={risky_body.get('message')!r}",
        )
        risky_id_run = risky_run.get("id")
        if risky_id_run:
            stopped = client.post(f"/api/executions/{risky_id_run}/stop")
            stopped_run = stopped.json()["run"] if stopped.status_code == 200 else {}
            check(
                "stopping an unconfirmed run does not claim progress",
                stopped.status_code == 200
                and stopped_run.get("state") == "completed"
                and stopped_run.get("last_verified_step", 0) == 0,
                f"state={stopped_run.get('state')} "
                f"last_verified_step={stopped_run.get('last_verified_step')}",
            )

        # 8. Voice/text intent path ---------------------------------------
        voice = client.post(
            "/api/voice/command",
            json={"transcript": "Continue my Python course", "source": "text"},
        )
        check(
            "voice/text command resolves to workflow.execute",
            voice.status_code == 200 and voice.json().get("intent") == "workflow.execute",
            f"intent={voice.json().get('intent')} reply={voice.json().get('reply_text')!r}",
        )

    time.sleep(0.2)
    print(f"\nPHASE_B_FAILURES={len(failures)}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
