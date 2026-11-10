"""Allowlisted browser execution starter. U3 owns this module."""

from typing import Any

from app.schemas.contracts import WorkflowStep


class BrowserExecutor:
    ALLOWED_ACTIONS = {
        "navigate_to_known_page",
        "click_semantic_target",
        "open_next_incomplete_item",
        "wait_for_visible_state",
        "read_progress_signal",
    }

    def execute(self, step: WorkflowStep) -> dict[str, Any]:
        if step.action_type not in self.ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported action type: {step.action_type}")
        raise NotImplementedError("U3: validate target and execute through Playwright")
