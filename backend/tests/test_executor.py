"""Unit tests for U3 browser executor. Deterministic; no real browser, no network."""

import pytest

from app.execution.executor import BrowserExecutor
from app.schemas.contracts import WorkflowStep


def _step(
    action_type: str = "click_semantic_target",
    target: dict | None = None,
    expected_result: dict | None = None,
    risk: str = "low",
) -> WorkflowStep:
    return WorkflowStep(
        id="step_001",
        order=1,
        action_type=action_type,
        target=target if target is not None else {"role": "link", "name": "Continue learning"},
        expected_result=expected_result if expected_result is not None else {"type": "navigation"},
        risk=risk,
    )


def test_unsupported_action_is_blocked():
    executor = BrowserExecutor()
    result = executor.execute(_step(action_type="submit_payment_form"))
    assert result["status"] == "blocked"
    assert result["verified"] is False
    assert result["error"]["code"] == "unsupported_action"


def test_missing_target_is_blocked():
    executor = BrowserExecutor()
    result = executor.execute(_step(target={}))
    assert result["status"] == "blocked"
    assert result["verified"] is False
    assert result["error"]["code"] == "ambiguous_or_missing_target"


def test_target_without_semantic_key_is_blocked():
    executor = BrowserExecutor()
    result = executor.execute(_step(target={"x": 100, "y": 200}))
    assert result["status"] == "blocked"
    assert result["verified"] is False


def test_valid_target_queued_without_browser_adapter():
    executor = BrowserExecutor()
    result = executor.execute(_step())
    assert result["status"] == "queued"
    assert result["verified"] is False
    assert result["action"]["action_type"] == "click_semantic_target"
    assert result["action"]["target"] == {"role": "link", "name": "Continue learning"}


def test_valid_target_performed_via_adapter_never_self_verified():
    def fake_adapter(action: dict) -> dict:
        return {"status": "performed", "browser": "chromium"}

    executor = BrowserExecutor(perform_action=fake_adapter)
    result = executor.execute(_step())
    assert result["status"] == "performed"
    assert result["verified"] is False
    assert result["step_id"] == "step_001"


def test_adapter_failure_propagates_verified_false():
    def failing_adapter(action: dict) -> dict:
        raise RuntimeError("page disconnected")

    executor = BrowserExecutor(perform_action=failing_adapter)
    with pytest.raises(RuntimeError):
        executor.execute(_step())
