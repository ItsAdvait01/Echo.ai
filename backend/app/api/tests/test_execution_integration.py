"""API-level execution safety tests.

These tests exercise the *route* rules (approval gating, confirmation gating,
checkpoint advancement) with deterministic executor/verifier doubles. The live
controlled-browser path is exercised separately by ``scripts/demo_teach.py`` and
``scripts/demo_execute.py``; nothing here claims to be browser execution.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.api.routes.workflows import stage_draft
from app.core.dependencies import get_browser_executor, get_checkpoint_verifier
from app.main import app
from app.schemas.contracts import AgentState, RiskLevel, WorkflowStep

client = TestClient(app)


class RecordingExecutor:
    """Executor double that records the steps the route asked it to perform."""

    def __init__(self, status: str = "performed") -> None:
        self.calls: list[WorkflowStep] = []
        self._status = status

    def execute(self, step: WorkflowStep) -> dict:
        self.calls.append(step)
        return {
            "success": True,
            "status": self._status,
            "page_url": "http://127.0.0.1:8765/lesson2.html",
            "page_title": "Python Course — Lesson 2 — Loops",
            "action_result": {"success": True},
        }


class StubVerifier:
    """Verifier double returning a fixed U4 status."""

    def __init__(self, status: str = "verified", confidence: str = "high") -> None:
        self.status = status
        self.confidence = confidence

    def verify(self, step: WorkflowStep, observed: dict) -> dict:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "reason": f"stub {self.status}",
            "observed": observed,
        }


def _workflow(
    *, risk: RiskLevel = RiskLevel.LOW, steps: int = 1, last_risk: RiskLevel | None = None
) -> str:
    """Stage and approve a workflow, returning its id."""
    built = [
        WorkflowStep(
            id=f"step-{index}",
            order=index,
            action_type="click_semantic_target",
            target={"test_id": f"next-lesson-{index}", "name": "Next lesson"},
            expected_result={"type": "navigation", "value": "lesson2", "match_mode": "contains"},
            risk=last_risk if (last_risk is not None and index == steps) else risk,
        )
        for index in range(1, steps + 1)
    ]
    draft = stage_draft(
        name=f"wf-{uuid.uuid4().hex[:6]}",
        description="",
        trigger_phrases=[],
        steps=built,
        uncertainty_notes=[],
    )
    approved = client.post(f"/api/workflows/{draft.id}/approve", json={"approved": True})
    assert approved.status_code == 200
    return draft.id


class Doubles:
    """Swappable executor/verifier doubles installed as route dependencies."""

    def __init__(self) -> None:
        self.executor = RecordingExecutor()
        self.verifier = StubVerifier()

    def install(self, **kwargs: object) -> Doubles:
        for key, value in kwargs.items():
            setattr(self, key, value)
        app.dependency_overrides[get_browser_executor] = lambda: self.executor
        app.dependency_overrides[get_checkpoint_verifier] = lambda: self.verifier
        return self


@pytest.fixture
def doubles() -> Iterator[Doubles]:
    """Install executor/verifier doubles and always clean them up."""
    installed = Doubles().install()
    try:
        yield installed
    finally:
        app.dependency_overrides.pop(get_browser_executor, None)
        app.dependency_overrides.pop(get_checkpoint_verifier, None)


def test_unapproved_workflow_cannot_execute() -> None:
    draft = stage_draft("draft only", "", [], [], [])
    response = client.post("/api/executions", json={"workflow_id": draft.id})
    assert response.status_code == 404  # drafts are not runnable memory


def test_high_risk_step_requires_explicit_confirmation(doubles: Doubles) -> None:
    executor = doubles.executor
    workflow_id = _workflow(risk=RiskLevel.HIGH)

    started = client.post("/api/executions", json={"workflow_id": workflow_id})
    assert started.status_code == 200
    body = started.json()
    assert body["run"]["state"] == AgentState.WAITING_FOR_CONFIRMATION
    assert body["run"]["last_verified_step"] == 0
    # The consequential action must not have been performed yet.
    assert executor.calls == []

    confirmed = client.post(f"/api/executions/{body['run']['id']}/confirm")
    assert confirmed.status_code == 200
    assert len(executor.calls) == 1
    assert confirmed.json()["run"]["last_verified_step"] == 1


def test_verified_step_advances_checkpoint(doubles: Doubles) -> None:
    workflow_id = _workflow()
    response = client.post("/api/executions", json={"workflow_id": workflow_id})
    body = response.json()
    assert body["verification"]["passed"] is True
    assert body["run"]["last_verified_step"] == 1
    assert body["run"]["state"] == AgentState.COMPLETED


@pytest.mark.parametrize("status", ["failed", "unknown", "needs_review"])
def test_failed_or_ambiguous_action_does_not_advance(doubles: Doubles, status: str) -> None:
    doubles.install(verifier=StubVerifier(status=status, confidence="low"))
    workflow_id = _workflow()
    body = client.post("/api/executions", json={"workflow_id": workflow_id}).json()
    assert body["run"]["last_verified_step"] == 0
    assert body["verification"]["passed"] is False
    assert body["run"]["state"] in (AgentState.PAUSED, AgentState.FAILED)


def test_run_advances_until_a_high_risk_step_needs_confirmation(doubles: Doubles) -> None:
    workflow_id = _workflow(steps=2, last_risk=RiskLevel.HIGH)
    body = client.post("/api/executions", json={"workflow_id": workflow_id}).json()

    # Step 1 ran without a second resume click; step 2 was staged, not performed.
    assert len(doubles.executor.calls) == 1
    assert body["run"]["last_verified_step"] == 1
    assert body["run"]["state"] == AgentState.WAITING_FOR_CONFIRMATION
    assert body["current_step"]["order"] == 2

    # The status endpoint reports the same staged step the UI must confirm.
    status = client.get(f"/api/executions/{body['run']['id']}").json()
    assert status["current_step"]["order"] == 2
    assert "awaits confirmation" in status["message"]

    confirmed = client.post(f"/api/executions/{body['run']['id']}/confirm").json()
    assert len(doubles.executor.calls) == 2
    assert confirmed["run"]["last_verified_step"] == 2
    assert confirmed["run"]["state"] == AgentState.COMPLETED


def test_verified_checkpoints_survive_a_later_failed_run(doubles: Doubles) -> None:
    workflow_id = _workflow(steps=2)

    first = client.post("/api/executions", json={"workflow_id": workflow_id}).json()
    # A low-risk workflow runs straight through in a single pass.
    assert first["run"]["last_verified_step"] == 2
    assert first["run"]["state"] == AgentState.COMPLETED

    doubles.install(verifier=StubVerifier(status="failed", confidence="low"))
    second = client.post(
        "/api/executions",
        json={"workflow_id": workflow_id, "resume_from_step": 1},
    ).json()

    # Step 2 failed, but the earlier verified checkpoint is still recorded.
    assert second["run"]["last_verified_step"] == 1
    assert second["run"]["state"] in (AgentState.PAUSED, AgentState.FAILED)
    stored = client.get(f"/api/workflows/{workflow_id}").json()["workflow"]
    assert len(stored["steps"]) == 2


def test_pause_and_stop_do_not_claim_completion(doubles: Doubles) -> None:
    workflow_id = _workflow(risk=RiskLevel.HIGH)
    run = client.post("/api/executions", json={"workflow_id": workflow_id}).json()["run"]

    paused = client.post(f"/api/executions/{run['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["run"]["state"] == AgentState.PAUSED
    assert paused.json()["run"]["last_verified_step"] == 0

    stopped = client.post(f"/api/executions/{run['id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["run"]["last_verified_step"] == 0
    assert "stopped by user" in stopped.json()["message"]
