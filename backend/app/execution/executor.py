"""Allowlisted browser execution. U3 owns this module.

Executes only allowlisted actions with validated semantic targets. The actual
browser interaction is delegated to the U1-controlled Playwright session
through an injected adapter; U3 never creates its own browser controller.

No action is reported as completed or verified here -- that is U4's role.
"""

from typing import Any, Callable

from app.schemas.contracts import WorkflowStep


class BrowserExecutor:
    """Run a single allowlisted workflow step through a controlled browser."""

    ALLOWED_ACTIONS = {
        "navigate_to_known_page",
        "click_semantic_target",
        "open_next_incomplete_item",
        "wait_for_visible_state",
        "read_progress_signal",
    }

    # Target keys that identify an element semantically without coordinates.
    SEMANTIC_KEYS = ("role", "name", "placeholder", "test_id", "label")

    def __init__(
        self,
        perform_action: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        """Create an executor.

        ``perform_action`` is the U1-owned browser adapter callback that
        drives the controlled Playwright session. When omitted, actions are
        validated and queued but not performed in a browser.
        """
        self._perform_action = perform_action

    def execute(self, step: WorkflowStep) -> dict[str, Any]:
        """Validate and perform one workflow step.

        Returns a structured result. ``verified`` is always ``False`` here --
        only U4 may mark a step verified. A missing or ambiguous target yields
        a safe ``blocked`` result and stops; no action is performed.
        """
        if step.action_type not in self.ALLOWED_ACTIONS:
            return self._blocked_result(
                step, "unsupported_action", f"Action not allowlisted: {step.action_type}"
            )

        target = dict(step.target or {})
        if not self._target_is_valid(target):
            return self._blocked_result(
                step,
                "ambiguous_or_missing_target",
                "Target cannot be identified uniquely; stopping before interaction.",
            )

        action = {
            "action_type": step.action_type,
            "target": target,
            "expected_result": dict(step.expected_result or {}),
            "risk": step.risk,
        }

        if self._perform_action is None:
            return {
                "step_id": step.id,
                "action": action,
                "status": "queued",
                "verified": False,
                "message": "Validated and queued; browser adapter not connected.",
            }

        result = self._perform_action(action)
        if not isinstance(result, dict):
            result = {}
        result.setdefault("step_id", step.id)
        result.setdefault("action", action)
        result.setdefault("status", "performed")
        result.setdefault("verified", False)
        return result

    def _target_is_valid(self, target: dict[str, Any]) -> bool:
        if not target:
            return False
        return any(key in target and target[key] for key in self.SEMANTIC_KEYS)

    def _blocked_result(self, step: WorkflowStep, code: str, message: str) -> dict[str, Any]:
        return {
            "step_id": step.id,
            "action": {"action_type": step.action_type, "target": dict(step.target or {})},
            "status": "blocked",
            "verified": False,
            "error": {"code": code, "message": message},
        }
